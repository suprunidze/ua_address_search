"""QGIS entry point, shared search flow and complete lifecycle cleanup."""

from pathlib import Path
from dataclasses import replace
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsCsException
from .core.client import RequestTask
from .core.cache import SessionCache
from .core.models import SearchResult
from .core.settings import Credentials, Settings
from .locator import AddressLocatorFilter
from .map_tools import MapDisplay, ReverseMapTool, download_geometry
from .ui.search_dock import SearchDock
from .ui.settings_dialog import SettingsDialog


class AddressSearchPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.settings = Settings()
        self.dock = None
        self.action = None
        self.locator = None
        self.display = None
        self.tool = None
        self.previous_tool = None
        self.records = []
        self.result_status = ""
        self.tasks = set()
        self.generations = {"search": 0, "feature": 0}
        self.active = False
        self.cache = SessionCache()

    def initGui(self):
        if self.active:
            return
        self.active = True
        if self.cache.closed:
            self.cache = SessionCache()
        self.dock = SearchDock(self.iface.mainWindow())
        self.dock.auto_geometry.setChecked(self.settings.auto_geometry())
        self.dock.geometry_toggled.connect(self.geometry_toggled)
        self.dock.quota_changed.connect(self.update_quota)
        self.cache.on_quota = self.dock.quota_changed.emit
        right = getattr(Qt, "RightDockWidgetArea", None)
        if right is None:
            right = Qt.DockWidgetArea.RightDockWidgetArea
        self.iface.addDockWidget(right, self.dock)
        self.display = MapDisplay(self.iface.mapCanvas())
        self.tool = ReverseMapTool(self.iface.mapCanvas())
        self.tool.coordinates_selected.connect(self.reverse_at)
        self.dock.search_requested.connect(self.search)
        self.dock.selection_requested.connect(self.select)
        self.dock.download_requested.connect(self.download_selected)
        self.dock.reverse_requested.connect(self.activate_reverse)
        self.dock.settings_requested.connect(self.configure)
        self.dock.cancel_requested.connect(self.cancel)
        icon = QIcon(str(Path(__file__).parent / "resources" / "icon.svg"))
        self.action = QAction(icon, "UA Address Search", self.iface.mainWindow())
        self.action.triggered.connect(self.open_panel)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("UA Address Search", self.action)
        self.locator = AddressLocatorFilter(
            activate=self.locator_activate,
            credentials_provider=self.refresh_credentials,
            cache=self.cache,
        )
        self.settings.migrate_locator_prefix()
        self.iface.registerLocatorFilter(self.locator)
        # Unlock only when the user requests search; loading QGIS must never prompt.
        self.dock.visibilityChanged.connect(self.visibility_changed)
        self.dock.setVisible(self.settings.visible())

    def visibility_changed(self, visible):
        self.settings.set_visible(visible)
        if not visible:
            self.cancel(silent=True)
            self.restore_tool()
            self.display.clear()

    def open_panel(self, checked=False):
        self.dock.show()
        self.dock.raise_()
        self.dock.query.setFocus()

    def refresh_credentials(self):
        try:
            credentials = self.settings.credentials()
        except ValueError as exc:
            self.dock.status.setText(str(exc))
            credentials = Credentials()
        changed = self.cache.identity != credentials.identity
        self.cache.set_identity(credentials.identity)
        if changed:
            self.update_quota()
        if self.locator is not None:
            self.locator.credentials = credentials
        return credentials

    def configure(self):
        self.cancel(silent=True)
        dialog = SettingsDialog(self.settings, self.iface.mainWindow())
        run = getattr(dialog, "exec", None) or dialog.exec_
        if run():
            self.refresh_credentials()
            self.select(self.dock.results.currentRow())
        dialog.deleteLater()

    def search(self, text):
        if len(text.strip()) < 3:
            self.dock.status.setText("Введіть щонайменше три символи адреси.")
            return
        self.start("search", text.strip())

    def cancel_tasks(self, channel=None, automatic_only=False):
        channels = [channel] if channel else list(self.generations)
        for current in channels:
            if not automatic_only:
                self.generations[current] += 1
        for task in list(self.tasks):
            if task.channel in channels and (not automatic_only or not task.download_requested):
                task.callback = None
                task.cancel()
                self.tasks.discard(task)
        if self.dock is not None:
            self.dock.busy(bool(self.tasks))

    def cancel(self, silent=False):
        active = bool(self.tasks) or (
            self.tool is not None and self.iface.mapCanvas().mapTool() == self.tool
        )
        self.cancel_tasks()
        if self.tool is not None:
            self.restore_tool()
        if self.dock is not None:
            self.dock.busy(False)
            if active and not silent:
                self.dock.status.setText("Запит скасовано.")

    def start(self, operation, value=None, download=False):
        credentials = self.refresh_credentials()
        identity = credentials.identity
        channel = "feature" if operation == "feature" else "search"
        key = self.cache.key(identity, operation, value)
        for pending in self.tasks:
            pending_identity = pending.credentials.identity
            if (
                not pending.feedback.isCanceled()
                and self.cache.key(pending_identity, pending.operation, pending.value) == key
            ):
                pending.download_requested = pending.download_requested or download
                return
        if operation == "feature":
            self.cancel_tasks("feature")
        else:
            found, cached_results = self.cache.peek(identity, operation, value)
            keep_geometry = (
                found
                and cached_results
                and any(
                    task.channel == "feature" and task.value == cached_results[0]["id"]
                    for task in self.tasks
                )
            )
            if keep_geometry:
                self.cancel_tasks("search")
                self.restore_tool()
            else:
                self.cancel(silent=True)
        if not credentials.url or not credentials.token:
            if credentials.url:
                self.dock.status.setText(
                    "Відкрийте налаштування й введіть власний ключ Visicom API."
                )
            return
        generation = self.generations[channel]
        self.dock.busy(True)
        if operation != "feature":
            self.dock.status.setText("Пошук…")

        def completed(task):
            self.tasks.discard(task)
            if not self.active or generation != self.generations[channel]:
                return
            self.dock.busy(bool(self.tasks))
            self.update_quota()
            if task.error:
                self.dock.status.setText(task.error)
                return
            if operation == "quota":
                remaining = task.result.get("remaining", "?")
                self.dock.status.setText(f"Доступно запитів сьогодні: {remaining} (Europe/Kyiv).")
            elif operation == "feature":
                if task.result and task.result[0].geometry:
                    if self.show_record(task.result[0], cached=False):
                        self.dock.status.setText(self.result_status + " • Геометрію показано.")
                        if task.download_requested:
                            try:
                                layer, added = download_geometry(task.result[0])
                                self.dock.status.setText(
                                    "Геометрію додано до шару: " + layer.name()
                                    if added
                                    else "Об’єкт уже є в тимчасовому шарі."
                                )
                            except ValueError as exc:
                                self.dock.status.setText(str(exc))
                else:
                    self.dock.status.setText("Геометрію не знайдено.")
            else:
                first_id = task.result[0].id if task.result else None
                if any(t.channel == "feature" and t.value != first_id for t in self.tasks):
                    self.cancel_tasks("feature")
                self.records = task.result
                self.display.clear()
                self.dock.show_results(self.records, operation == "reverse")
                self.result_status = self.dock.status.text()
                if self.records:
                    self.dock.results.setCurrentRow(0)
            if task.source != "network":
                self.dock.status.setText(self.dock.status.text() + " • З кешу сесії")

        task = RequestTask(credentials, operation, value, completed, self.cache)
        task.download_requested = download
        task.channel = channel
        self.tasks.add(task)
        QgsApplication.taskManager().addTask(task)

    def select(self, index):
        if 0 <= index < len(self.records):
            record = self.records[index]
            if any(t.channel == "feature" and t.value != record.id for t in self.tasks):
                self.cancel_tasks("feature")
            self.show_record(record)
            if self.dock.auto_geometry.isChecked():
                found, cached = self.cache.peek(self.cache.identity, "feature", record.id)
                if found:
                    self.dock.status.setText(self.result_status + " • З кешу сесії")
                else:
                    self.start("feature", record.id)

    def show_record(self, record, cached=True):
        try:
            if cached and self.dock.auto_geometry.isChecked() and self.cache.identity is not None:
                found, records = self.cache.peek(self.cache.identity, "feature", record.id)
                if found and records:
                    record = SearchResult.from_dict(records[0])
            if cached and not self.dock.auto_geometry.isChecked():
                record = replace(record, geometry=None)
            self.display.show(record)
            return True
        except (QgsCsException, ValueError) as exc:
            self.dock.status.setText("Не вдалося показати об’єкт: " + str(exc))
            return False

    def geometry_toggled(self, enabled):
        self.settings.set_auto_geometry(enabled)
        if not enabled:
            self.cancel_tasks("feature", automatic_only=True)
            index = self.dock.results.currentRow()
            if 0 <= index < len(self.records):
                self.show_record(self.records[index])
        else:
            self.select(self.dock.results.currentRow())

    def update_quota(self):
        if self.dock is None:
            return
        with self.cache.lock:
            quota = dict(self.cache.quota) if self.cache.quota else None
        if not quota:
            self.dock.quota.setText("Квота: ще немає даних")
            return
        if quota.get("local") and not self.settings.show_quota():
            text = f"Виконано запитів сьогодні: {quota.get('used', 0)}"
        else:
            prefix = "Оцінка залишку" if quota.get("local") else "Доступно сьогодні"
            text = (
                f"{prefix}: {quota.get('remaining', '?')} / {quota.get('token_daily_limit', '?')}"
            )
            if quota.get("exceeded"):
                text += f" • Перевищення: {quota['exceeded']}"
        self.dock.quota.setText(text)
        self.dock.quota.setToolTip(
            "Europe/Kyiv • "
            + str(quota.get("day", ""))
            + (
                " • Лише цей плагін на цьому комп’ютері; перевищення не блокує запити."
                if quota.get("local")
                else " • З урахуванням спільного ліміту сервера."
            )
        )

    def show_geometry(self, index):
        if 0 <= index < len(self.records):
            self.start("feature", self.records[index].id)

    def download_selected(self, index):
        if 0 <= index < len(self.records):
            self.start("feature", self.records[index].id, download=True)

    def activate_reverse(self):
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() != self.tool:
            self.previous_tool = canvas.mapTool()
            canvas.setMapTool(self.tool)
        self.dock.cancel_button.setEnabled(True)
        self.dock.status.setText("Клацніть на карті: найближча адреса в радіусі 50 м.")

    def restore_tool(self):
        canvas = self.iface.mapCanvas()
        if self.tool is not None and canvas.mapTool() == self.tool:
            if self.previous_tool is not None:
                canvas.setMapTool(self.previous_tool)
            else:
                canvas.unsetMapTool(self.tool)
        self.previous_tool = None

    def reverse_at(self, map_point):
        try:
            coordinates = self.display.wgs84(map_point)
        except QgsCsException:
            self.dock.status.setText("Не вдалося перетворити координати кліку у WGS84.")
            return
        self.restore_tool()
        self.start("reverse", coordinates)

    def locator_activate(self, data):
        if not self.active:
            return
        self.open_panel()
        if data.get("error"):
            self.configure()
            return
        try:
            record = SearchResult.from_dict(data)
        except ValueError:
            self.dock.status.setText("Некоректний результат Locator.")
            return
        self.cancel(silent=True)
        self.records = [record]
        self.dock.show_results(self.records)
        self.result_status = self.dock.status.text()
        self.dock.results.setCurrentRow(0)

    def unload(self):
        if not self.active:
            return
        self.active = False
        self.cancel(silent=True)
        self.cache.on_quota = None
        self.cache.close()
        self.restore_tool()
        self.display.clear()
        if self.locator is not None:
            self.locator.activate = None
            self.iface.deregisterLocatorFilter(self.locator)
            self.locator = None
        self.iface.removePluginMenu("UA Address Search", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action.triggered.disconnect()
        self.action.deleteLater()
        self.dock.visibilityChanged.disconnect(self.visibility_changed)
        self.iface.removeDockWidget(self.dock)
        self.dock.close()
        self.dock.deleteLater()
        self.tool.coordinates_selected.disconnect(self.reverse_at)
        self.tool.deleteLater()
        self.dock = None
        self.action = None
        self.tool = None
        self.display = None
        self.records = []
