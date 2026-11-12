"""QGIS entry point, shared search flow and complete lifecycle cleanup."""

from pathlib import Path
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsCsException
from .core.client import RequestTask
from .core.cache import SessionCache, namespace
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
        self.tasks = set()
        self.generation = 0
        self.active = False
        self.cache = SessionCache()

    def initGui(self):
        if self.active:
            return
        self.active = True
        if self.cache.closed:
            self.cache = SessionCache()
        self.dock = SearchDock(self.iface.mainWindow())
        right = getattr(Qt, "RightDockWidgetArea", None)
        if right is None:
            right = Qt.DockWidgetArea.RightDockWidgetArea
        self.iface.addDockWidget(right, self.dock)
        self.display = MapDisplay(self.iface.mapCanvas())
        self.tool = ReverseMapTool(self.iface.mapCanvas())
        self.tool.coordinates_selected.connect(self.reverse_at)
        self.dock.search_requested.connect(self.search)
        self.dock.selection_requested.connect(self.select)
        self.dock.geometry_requested.connect(self.show_geometry)
        self.dock.download_requested.connect(self.download_selected)
        self.dock.reverse_requested.connect(self.activate_reverse)
        self.dock.settings_requested.connect(self.configure)
        self.dock.cancel_requested.connect(self.cancel)
        self.dock.quota_requested.connect(lambda: self.start("quota"))
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
            self.cancel()
            self.restore_tool()
            self.display.clear()

    def open_panel(self, checked=False):
        self.dock.show()
        self.dock.raise_()
        self.dock.query.setFocus()
        self.refresh_credentials()

    def refresh_credentials(self):
        try:
            credentials = self.settings.credentials()
        except ValueError as exc:
            self.dock.status.setText(str(exc))
            credentials = Credentials()
        self.cache.set_identity(namespace(credentials.url, credentials.token))
        if self.locator is not None:
            self.locator.credentials = credentials
        return credentials

    def configure(self):
        self.cancel()
        dialog = SettingsDialog(self.settings, self.iface.mainWindow())
        run = getattr(dialog, "exec", None) or dialog.exec_
        run()
        self.refresh_credentials()
        dialog.deleteLater()

    def search(self, text):
        if len(text.strip()) < 3:
            self.dock.status.setText("Введіть щонайменше три символи адреси.")
            return
        self.start("search", text.strip())

    def cancel(self):
        self.generation += 1
        for task in list(self.tasks):
            task.callback = None
            task.cancel()
        self.tasks.clear()
        if self.tool is not None:
            self.restore_tool()
        if self.dock is not None:
            self.dock.busy(False)
            self.dock.status.setText("Запит скасовано.")

    def start(self, operation, value=None, download=False):
        credentials = self.refresh_credentials()
        identity = namespace(credentials.url, credentials.token)
        key = self.cache.key(identity, operation, value)
        for pending in self.tasks:
            pending_identity = namespace(pending.credentials.url, pending.credentials.token)
            if (
                not pending.feedback.isCanceled()
                and self.cache.key(pending_identity, pending.operation, pending.value) == key
            ):
                pending.download_requested = pending.download_requested or download
                return
        self.cancel()
        if not credentials.url or not credentials.token:
            self.dock.status.setText("Відкрийте налаштування: потрібні URL сервера й токен.")
            return
        generation = self.generation
        self.dock.busy(True)

        def completed(task):
            self.tasks.discard(task)
            if not self.active or generation != self.generation:
                return
            self.dock.busy(False)
            if task.error:
                self.dock.status.setText(task.error)
                return
            if operation == "quota":
                remaining = task.result.get("remaining", "?")
                self.dock.status.setText(f"Доступно запитів сьогодні: {remaining} (Europe/Kyiv).")
            elif operation == "feature":
                if task.result:
                    if self.show_record(task.result[0]):
                        self.dock.status.setText("Геометрію показано.")
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
                self.records = task.result
                self.display.clear()
                self.dock.show_results(self.records, operation == "reverse")
                if len(self.records) == 1:
                    self.dock.results.setCurrentRow(0)
            if task.source != "network":
                self.dock.status.setText(self.dock.status.text() + " • З кешу сесії")

        task = RequestTask(credentials, operation, value, completed, self.cache)
        task.download_requested = download
        self.tasks.add(task)
        QgsApplication.taskManager().addTask(task)

    def select(self, index):
        if 0 <= index < len(self.records):
            self.show_record(self.records[index])

    def show_record(self, record):
        try:
            if self.cache.identity is not None:
                found, records = self.cache.peek(self.cache.identity, "feature", record.id)
                if found and records:
                    record = SearchResult.from_dict(records[0])
            self.display.show(record)
            return True
        except (QgsCsException, ValueError) as exc:
            self.dock.status.setText("Не вдалося показати об’єкт: " + str(exc))
            return False

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
        self.cancel()
        self.records = [record]
        self.dock.show_results(self.records)
        self.dock.results.setCurrentRow(0)

    def unload(self):
        if not self.active:
            return
        self.active = False
        self.cancel()
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
