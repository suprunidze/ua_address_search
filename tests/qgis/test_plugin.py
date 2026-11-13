from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from dataclasses import replace
import tempfile
import uuid
import threading
import time
import unittest
from unittest.mock import patch
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import QApplication, QMainWindow
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeedback,
    QgsLocator,
    QgsLocatorContext,
    QgsPointXY,
    QgsSettings,
    QgsProject,
    QgsNetworkAccessManager,
    QgsRectangle,
)
from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsLayerTreeMapCanvasBridge
from ua_address_search import classFactory
from ua_address_search.core.client import ClientError, NetworkClient, RequestTask
from ua_address_search.core.cache import SessionCache
from ua_address_search.core.models import SearchResult
from ua_address_search.core.settings import (
    Credentials,
    PREFIX,
    Settings,
    validate_url,
    LOCATOR_PREFIX_KEYS,
)
from ua_address_search.locator import AddressLocatorFilter
from ua_address_search.map_tools import MapDisplay, download_geometry
from ua_address_search.ui.settings_dialog import SettingsDialog
from ua_address_search.ui.quota_dialog import REGISTRATION_URL

RECORD = SearchResult(
    "ADR_TEST",
    "Київ, Хрещатик, 26",
    "Київ",
    "adr_address",
    (30.5234, 50.4501),
    (30.523, 50.449, 30.524, 50.451),
)


def events():
    QgsApplication.processEvents()
    kind = getattr(QEvent, "DeferredDelete", None)
    if kind is None:
        kind = QEvent.Type.DeferredDelete
    QCoreApplication.sendPostedEvents(None, kind)


def wait_until(predicate, seconds=6):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        events()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Iface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.locator = QgsLocator()
        self.actions = []
        self.filters = []
        self.bridge = None

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def layerTreeCanvasBridge(self):
        return self.bridge

    def addDockWidget(self, area, dock):
        self.window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.window.removeDockWidget(dock)

    def addToolBarIcon(self, action):
        self.actions.append(action)

    def removeToolBarIcon(self, action):
        self.actions.remove(action)

    def addPluginToMenu(self, menu, action):
        pass

    def removePluginMenu(self, menu, action):
        pass

    def registerLocatorFilter(self, item):
        self.filters.append(item)
        self.locator.registerFilter(item)

    def deregisterLocatorFilter(self, item):
        self.filters.remove(item)
        self.locator.deregisterFilter(item)

    def close(self):
        self.window.close()
        self.window.deleteLater()
        events()


class Handler(BaseHTTPRequestHandler):
    calls = []
    sessions = []
    geometry_gate = None
    geometry_started = threading.Event()
    quota_day = "2026-11-12"
    error_gate = None
    error_started = threading.Event()

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).calls.append((self.path, self.headers.get("Authorization"), body))
        type(self).sessions.append(self.headers.get("X-UAAS-Session-ID"))
        if self.path.startswith("/slow/"):
            time.sleep(0.4)
        code = 200
        if self.path.startswith("/denied/"):
            gate = type(self).error_gate
            if gate is not None:
                type(self).error_started.set()
                gate.wait(4)
            code, content = 429, {"error": {"code": "daily_quota"}}
        elif self.path.startswith("/minute/"):
            code, content = 429, {"error": {"code": "rate_limit"}}
        elif self.path.startswith("/provider/"):
            code, content = 429, {"error": {"code": "provider_quota"}}
        elif self.path.startswith("/bad/"):
            content = None
        elif self.path.startswith("/direct/"):
            content = json.loads(
                (Path(__file__).resolve().parents[1] / "fixtures/address.json").read_text(
                    encoding="utf-8"
                )
            )
        elif self.path.startswith("/v1/register"):
            content = {"registered": True}
        elif self.path.startswith("/v1/quota"):
            content = {"remaining": 97}
        elif "/v1/features/" in self.path:
            record = RECORD.to_dict()
            record["id"] = self.path.rsplit("/", 1)[-1]
            gate = type(self).geometry_gate
            if record["id"] == "ADR_HOLD" and gate is not None:
                type(self).geometry_started.set()
                gate.wait(4)
            if record["id"] == "ADR_SLOW":
                time.sleep(0.35)
            record["geometry"] = {
                "type": "Polygon",
                "coordinates": [[[30.52, 50.45], [30.53, 50.45], [30.53, 50.46], [30.52, 50.45]]],
            }
            content = {"results": [record]}
        else:
            content = {"results": [RECORD.to_dict()]}
        if isinstance(content, dict) and not self.path.startswith("/direct/"):
            content["quota"] = {
                "observed_at": time.time_ns(),
                "remaining": 0
                if content.get("error", {}).get("code") == "daily_quota"
                else 100 - len(type(self).calls),
                "token_daily_limit": 100,
                "day": type(self).quota_day,
            }
        data = json.dumps(content).encode() if content is not None else b"not JSON"
        if self.path.startswith("/redirect/"):
            code = 302
        self.send_response(code)
        if code == 302:
            self.send_header("Location", "/v1/search")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class QgisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.iface = Iface()
        self.plugin = None
        Handler.calls.clear()
        Handler.sessions.clear()
        Handler.geometry_gate = None
        Handler.geometry_started.clear()
        Handler.quota_day = "2026-11-12"
        Handler.error_gate = None
        Handler.error_started.clear()
        QgsSettings().remove(PREFIX)
        for key in LOCATOR_PREFIX_KEYS:
            QgsSettings().remove(key)

    def tearDown(self):
        if self.plugin:
            self.plugin.unload()
        QgsProject.instance().removeAllMapLayers()
        self.iface.close()
        QgsSettings().remove(PREFIX)
        for key in LOCATOR_PREFIX_KEYS:
            QgsSettings().remove(key)
        events()

    def test_lifecycle_and_reopen(self):
        self.plugin = classFactory(self.iface)
        for _ in range(2):
            self.plugin.initGui()
            self.plugin.initGui()
            self.assertEqual(len(self.iface.actions), 1)
            self.assertEqual(len(self.iface.filters), 1)
            self.plugin.dock.query.setText("Київ")
            self.plugin.unload()
            self.plugin.unload()
            events()
            self.assertEqual(self.iface.actions, [])
            self.assertEqual(self.iface.filters, [])
            self.assertEqual(self.plugin.tasks, set())

    def test_reverse_after_previous_map_tool_deleted(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        self.plugin.activate_reverse()
        self.plugin.activate_reverse()
        self.assertIs(self.plugin.previous_tool, previous)
        sip.delete(previous)
        self.assertTrue(sip.isdeleted(previous))
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            self.plugin.reverse_at(QgsPointXY(30.5234, 50.4501))
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
        self.assertIs(self.plugin.previous_tool, previous)
        self.assertEqual(self.iface.canvas.mapTool(), self.plugin.tool)
        self.assertEqual(sum(path == "/v1/reverse" for path, _, _ in Handler.calls), 1)
        self.assertIsNotNone(self.plugin.display.marker)
        self.plugin.set_reverse_enabled(False)
        self.assertIsNone(self.plugin.previous_tool)
        self.assertNotEqual(self.iface.canvas.mapTool(), self.plugin.tool)

    def test_deleted_previous_tool_cancel_hide_and_unload(self):
        self.plugin = classFactory(self.iface)
        for action in ("cancel", "hide", "unload"):
            with self.subTest(action=action):
                self.plugin.initGui()
                previous = QgsMapToolPan(self.iface.canvas)
                self.iface.canvas.setMapTool(previous)
                self.plugin.activate_reverse()
                active_tool = self.plugin.tool
                sip.delete(previous)
                if action == "hide":
                    self.plugin.visibility_changed(False)
                else:
                    getattr(self.plugin, action)()
                self.assertIsNone(self.plugin.previous_tool)
                self.assertNotEqual(self.iface.canvas.mapTool(), active_tool)
        self.plugin.initGui()
        self.plugin.activate_reverse()
        self.plugin.cancel()
        self.assertEqual(len(self.iface.filters), 1)
        self.assertIsNone(self.plugin.previous_tool)

    def test_restore_preserves_live_tool_or_user_selection(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        self.plugin.activate_reverse()
        self.plugin.restore_tool()
        self.assertIs(self.iface.canvas.mapTool(), previous)
        self.assertIsNone(self.plugin.previous_tool)
        self.plugin.activate_reverse()
        selected = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(selected)
        sip.delete(previous)
        self.plugin.restore_tool()
        self.assertIs(self.iface.canvas.mapTool(), selected)
        self.assertIsNone(self.plugin.previous_tool)
        self.iface.canvas.unsetMapTool(selected)
        self.plugin.activate_reverse()
        self.plugin.restore_tool()
        self.assertIsNone(self.iface.canvas.mapTool())

    def test_restore_handles_deletion_during_switch_and_propagates_other_errors(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        self.plugin.activate_reverse()

        def delete_on_restore(tool):
            sip.delete(tool)
            raise RuntimeError("deleted during switch")

        with patch.object(self.iface.canvas, "setMapTool", delete_on_restore):
            self.plugin.restore_tool()
        self.assertIsNone(self.iface.canvas.mapTool())
        self.assertIsNone(self.plugin.previous_tool)
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        self.plugin.activate_reverse()
        with patch.object(
            self.iface.canvas, "setMapTool", side_effect=RuntimeError("unexpected restore failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected restore failure"):
                self.plugin.restore_tool()
        self.assertIsNone(self.plugin.previous_tool)
        self.assertFalse(sip.isdeleted(previous))

    def test_panel_order_labels_and_help_keeps_map_state(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        dock = self.plugin.dock
        self.assertEqual(
            [dock.search_row.itemAt(i).widget() for i in range(4)],
            [dock.reverse_button, dock.query, dock.search_button, dock.cancel_button],
        )
        self.assertEqual(
            [dock.action_row.itemAt(i).widget() for i in (0, 1, 3, 4)],
            [dock.download_button, dock.copy_button, dock.settings_button, dock.help_button],
        )
        layout = dock.widget().layout()
        self.assertEqual(layout.indexOf(dock.status) + 1, layout.indexOf(dock.auto_geometry))
        styles = getattr(Qt, "ToolButtonStyle", Qt)
        for button in (dock.download_button, dock.settings_button):
            self.assertEqual(button.toolButtonStyle(), styles.ToolButtonTextBesideIcon)
        self.assertEqual(dock.help_button.toolButtonStyle(), styles.ToolButtonIconOnly)
        self.assertEqual(dock.help_button.accessibleName(), "Допомога")
        self.assertFalse(dock.help_button.icon().isNull())
        self.assertTrue(dock.reverse_button.isCheckable())
        self.assertFalse(dock.reverse_button.isChecked())
        self.plugin.activate_reverse()
        self.plugin.show_record(RECORD)
        marker = self.plugin.display.marker
        with patch(
            "ua_address_search.plugin.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            dock.help_button.click()
            url = open_url.call_args[0][0]
            self.assertTrue(url.isLocalFile())
            self.assertTrue(Path(url.toLocalFile()).is_file())
            self.assertEqual(Path(url.toLocalFile()).name, "help.html")
        self.assertEqual(self.iface.canvas.mapTool(), self.plugin.tool)
        self.assertIs(self.plugin.display.marker, marker)
        self.assertEqual(Handler.calls, [])
        with patch("ua_address_search.plugin.QDesktopServices.openUrl", return_value=False):
            dock.help_button.click()
            self.assertIn("Не вдалося", dock.status.text())
        with patch("ua_address_search.plugin.Path.is_file", return_value=False):
            with patch("ua_address_search.plugin.QDesktopServices.openUrl") as open_url:
                dock.help_button.click()
                open_url.assert_not_called()
                self.assertIn("відсутній", dock.status.text())

    def test_reverse_toggle_repeated_clicks_and_other_tool(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        button = self.plugin.dock.reverse_button
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            button.click()
            self.assertTrue(button.isChecked())
            for point in (QgsPointXY(30, 50), QgsPointXY(31, 51)):
                self.plugin.reverse_at(point)
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertTrue(button.isChecked())
                self.assertEqual(self.iface.canvas.mapTool(), self.plugin.tool)
            self.plugin.search("Київ")
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertTrue(button.isChecked())
        self.assertEqual(sum(path == "/v1/reverse" for path, _, _ in Handler.calls), 2)
        button.click()
        self.assertIs(self.iface.canvas.mapTool(), previous)
        self.assertFalse(button.isChecked())
        button.click()
        selected = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(selected)
        self.assertFalse(button.isChecked())
        self.plugin.cancel()
        self.assertIs(self.iface.canvas.mapTool(), selected)

    def test_cancel_clears_only_own_overlays_and_keeps_cache_and_layers(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        other = MapDisplay(self.iface.canvas)
        other.show(RECORD)
        try:
            with patch.object(
                Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
            ):
                self.plugin.search("Київ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.plugin.download_selected(0)
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                layers = list(QgsProject.instance().mapLayers().values())
                self.assertTrue(self.plugin.dock.cancel_button.isEnabled())
                self.assertTrue(self.plugin.display.flashes)
                calls = len(Handler.calls)
                self.plugin.cancel()
                self.assertFalse(self.plugin.display.has_overlays())
                self.assertFalse(self.plugin.dock.cancel_button.isEnabled())
                self.assertEqual(self.plugin.records, [RECORD])
                self.assertEqual(layers[0].featureCount(), 1)
                self.assertIsNotNone(other.marker)
                for geometry in (
                    {"type": "Point", "coordinates": [30, 50]},
                    {"type": "LineString", "coordinates": [[30, 50], [31, 51]]},
                    {"type": "Polygon", "coordinates": [[[30, 50], [31, 50], [31, 51], [30, 50]]]},
                ):
                    self.plugin.show_record(replace(RECORD, geometry=geometry), cached=False)
                    self.assertIsNotNone(self.plugin.display.band)
                    self.plugin.cancel()
                    self.assertFalse(self.plugin.display.has_overlays())
                    self.assertIsNotNone(other.marker)
                self.assertTrue(wait_until(lambda: not other.flashes, seconds=3))
                self.assertFalse(self.plugin.display.has_overlays())
                self.plugin.search("КИЇВ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertEqual(len(Handler.calls), calls)
                self.assertIsNotNone(self.plugin.display.band)
                self.plugin.cancel()
                self.plugin.search("Повільний пошук")
                self.plugin.cancel()
                self.assertTrue(
                    wait_until(lambda: QgsApplication.taskManager().countActiveTasks() == 0)
                )
                self.assertFalse(self.plugin.display.has_overlays())
        finally:
            other.clear()

    def test_settings_adapt_to_visible_rows_and_larger_font(self):
        dialog = SettingsDialog(Settings(), self.iface.window)
        try:
            dialog.show()
            events()
            railway_size = dialog.size()
            self.assertGreaterEqual(dialog.width(), 500)
            self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
            dialog.resize(dialog.width() + 200, dialog.height() + 200)
            self.assertEqual(dialog.size(), railway_size)
            self.assertTrue(dialog.size().expandedTo(dialog.minimumSize()) == dialog.size())
            dialog.mode.setCurrentIndex(1)
            events()
            self.assertTrue(dialog.direct.isVisible())
            self.assertGreater(dialog.height(), railway_size.height())
            font = QFont(dialog.font())
            font.setPointSize(16)
            dialog.setFont(font)
            events()
            self.assertGreaterEqual(dialog.width(), dialog.minimumSizeHint().width())
            for widget in (dialog.mode, dialog.key, dialog.show_quota, dialog.limit):
                self.assertTrue(widget.isVisible())
                self.assertGreaterEqual(widget.height(), widget.minimumSizeHint().height())
            larger = dialog.size()
            dialog.mode.setCurrentIndex(0)
            events()
            self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
            self.assertGreaterEqual(dialog.width(), 500)
            self.assertNotEqual(dialog.size(), larger)
            # Fewer rows can be taller when the layout also becomes narrower.
            expected = dialog.layout().totalHeightForWidth(dialog.width())
            self.assertEqual(dialog.height(), max(expected, dialog.layout().minimumSize().height()))
        finally:
            dialog.close()
            dialog.deleteLater()
            events()

    def canvas_view(self):
        canvas = self.iface.canvas
        return (
            QgsRectangle(canvas.extent()).toString(12),
            canvas.scale(),
            canvas.mapSettings().destinationCrs().authid(),
            canvas.rotation(),
        )

    def test_download_preserves_view_with_real_first_layer_bridge_and_cache(self):
        project = QgsProject.instance()
        old_crs = project.crs()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.iface.bridge = QgsLayerTreeMapCanvasBridge(
            project.layerTreeRoot(), self.iface.canvas, self.iface.window
        )
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.plugin.dock.auto_geometry.setChecked(False)
        try:
            with patch.object(
                Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
            ):
                self.plugin.search("Київ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertFalse(project.mapLayers())
                self.iface.canvas.setRotation(17)
                self.iface.canvas.setExtent(QgsRectangle(1e6, 2e6, 1.4e6, 2.3e6))
                before = self.canvas_view()
                self.plugin.download_selected(0)
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                events()
                self.assertEqual(self.canvas_view(), before)
                self.assertEqual(project.crs().authid(), "EPSG:3857")
                self.assertTrue(self.iface.bridge.autoSetupOnFirstLayer())
                self.assertEqual(len(project.mapLayers()), 1)
                self.assertIsNotNone(self.plugin.display.band)
                self.iface.canvas.setExtent(QgsRectangle(2e6, 1e6, 2.4e6, 1.3e6))
                before = self.canvas_view()
                self.plugin.download_selected(0)
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                events()
                self.assertEqual(self.canvas_view(), before)
                self.assertEqual(len(Handler.calls), 2)
                self.assertEqual(next(iter(project.mapLayers().values())).featureCount(), 1)
                with patch(
                    "ua_address_search.plugin.download_geometry",
                    side_effect=ValueError("test failure"),
                ):
                    self.plugin.download_selected(0)
                    self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                    self.assertEqual(self.canvas_view(), before)
                self.plugin.select(0)
                self.assertNotEqual(self.canvas_view(), before)
        finally:
            project.setCrs(old_crs)

    def test_download_joins_pending_geometry_and_preserves_user_pan(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.plugin.records = [replace(RECORD, id="ADR_HOLD")]
        self.plugin.dock.show_results(self.plugin.records)
        Handler.geometry_gate = threading.Event()
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            try:
                self.plugin.dock.results.setCurrentRow(0)
                self.assertTrue(wait_until(Handler.geometry_started.is_set))
                self.iface.canvas.setExtent(QgsRectangle(1e6, 2e6, 1.4e6, 2.3e6))
                before = self.canvas_view()
                self.plugin.download_selected(0)
                self.assertEqual(len(self.plugin.tasks), 1)
            finally:
                Handler.geometry_gate.set()
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(self.canvas_view(), before)
            self.assertEqual(len(Handler.calls), 1)
            self.assertEqual(len(QgsProject.instance().mapLayers()), 1)

    def test_quota_dialog_once_per_day_all_panel_routes_and_registration_link(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        credentials = Credentials(self.url + "/denied", "synthetic-token")
        with patch.object(Settings, "credentials", return_value=credentials):
            with patch(
                "ua_address_search.ui.quota_dialog.QDesktopServices.openUrl", return_value=True
            ) as browser:
                for operation, value in (
                    ("search", "Київ"),
                    ("reverse", (30, 50)),
                    ("feature", RECORD.id),
                ):
                    self.plugin.start(operation, value)
                    self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                    events()
                    dialog = self.plugin.quota_dialog
                    self.assertIsNotNone(dialog)
                    if operation == "search":
                        self.assertTrue(dialog.isVisible())
                        self.assertFalse(dialog.isModal())
                        self.assertNotIn("персональ", dialog.message.text())
                        self.assertNotIn("загальн", dialog.message.text())
                        browser.assert_not_called()
                        dialog.registration.linkActivated.emit(REGISTRATION_URL)
                        self.assertEqual(browser.call_args[0][0].toString(), REGISTRATION_URL)
                        with patch.object(self.plugin, "configure") as configure:
                            dialog.settings_button.click()
                            configure.assert_called_once_with(mode="visicom")
                    else:
                        self.assertFalse(dialog.isVisible())
                    self.assertEqual(len(self.plugin.quota_notices), 1)
                Handler.quota_day = "2026-11-13"
                self.plugin.search("Київ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertTrue(self.plugin.quota_dialog.isVisible())
                self.assertEqual(len(self.plugin.quota_notices), 2)

    def test_quota_dialog_from_real_locator_and_no_notice_for_other_errors(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        with patch.object(
            Settings,
            "credentials",
            return_value=Credentials(self.url + "/denied", "synthetic-token"),
        ):
            self.iface.locator.fetchResults("адр Київ", QgsLocatorContext())
            self.assertTrue(wait_until(lambda: self.plugin.quota_dialog is not None))
        self.plugin.quota_dialog.close()
        notices = len(self.plugin.quota_notices)
        for path in ("/minute", "/provider"):
            with patch.object(
                Settings,
                "credentials",
                return_value=Credentials(self.url + path, "synthetic-token"),
            ):
                self.plugin.search("Київ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertFalse(self.plugin.quota_dialog.isVisible())
                self.assertEqual(len(self.plugin.quota_notices), notices)

    def test_cancelled_stale_and_direct_errors_do_not_open_quota_dialog(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        Handler.error_gate = threading.Event()
        with patch.object(
            Settings,
            "credentials",
            return_value=Credentials(self.url + "/denied", "synthetic-token"),
        ):
            try:
                self.plugin.search("Київ")
                self.assertTrue(wait_until(Handler.error_started.is_set))
                self.plugin.cancel()
            finally:
                Handler.error_gate.set()
            self.assertTrue(
                wait_until(lambda: QgsApplication.taskManager().countActiveTasks() == 0)
            )
            self.assertIsNone(self.plugin.quota_dialog)
        feedback = QgsFeedback()
        notice = {
            "identity": self.plugin.cache.identity,
            "code": "daily_quota",
            "day": "2026-11-12",
            "feedback": feedback,
        }
        feedback.cancel()
        self.plugin.access_error(notice)
        self.assertIsNone(self.plugin.quota_dialog)
        notice["feedback"] = QgsFeedback()
        notice["identity"] = ("stale", "identity")
        self.plugin.access_error(notice)
        self.assertIsNone(self.plugin.quota_dialog)
        notice["identity"] = self.plugin.cache.identity
        with patch.object(Settings, "mode", return_value="visicom"):
            self.plugin.access_error(notice)
            self.assertIsNone(self.plugin.quota_dialog)
        self.plugin.unload()
        self.plugin.access_error(notice)
        self.assertIsNone(self.plugin.quota_dialog)

    def test_settings_open_own_key_without_saving_access_mode(self):
        settings = Settings()
        self.assertEqual(settings.mode(), "railway")
        dialog = SettingsDialog(settings, self.iface.window, initial_mode="visicom")
        try:
            self.assertEqual(dialog.mode.currentData(), "visicom")
            self.assertEqual(settings.mode(), "railway")
            self.assertEqual(dialog.key.text(), "")
            self.assertGreaterEqual(dialog.width(), 500)
            self.assertEqual(dialog.minimumSize(), dialog.maximumSize())
        finally:
            dialog.reject()
            dialog.deleteLater()
            events()
        self.assertEqual(settings.mode(), "railway")

    def test_encrypted_settings_and_url_validation(self):
        manager = QgsApplication.authManager()
        self.assertTrue(manager.setMasterPassword("uaas-test-master-password", True))
        settings = Settings()
        try:
            settings.save(self.url, "synthetic-private-token")
            self.assertEqual(settings.credentials().token, "synthetic-private-token")
            self.assertNotIn("token", settings.credentials().__repr__())
            settings.save(self.url, "updated-private-token")
            self.assertEqual(settings.credentials().token, "updated-private-token")
            settings.set_visible(True)
            self.assertTrue(Settings().visible())
            with self.assertRaises(ValueError):
                settings.save("https://other.invalid", "")
            self.assertFalse(
                any(
                    "synthetic-private-token" in str(QgsSettings().value(k))
                    for k in QgsSettings().allKeys()
                )
            )
        finally:
            identifier = str(QgsSettings().value(PREFIX + "authcfg", ""))
            if identifier:
                manager.removeAuthenticationConfig(identifier)
        for url in ("http://example.com", "https://user:pass@example.com", "https://x/?key=x"):
            with self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url("https://example.com/"), "https://example.com")

    def test_network_errors_cancellation_and_headers(self):
        client = NetworkClient()
        with self.assertRaisesRegex(ClientError, "Налаштуйте доступ"):
            client.request(Credentials(), "search", "Київ")
        credentials = Credentials(self.url, "synthetic-token")
        self.assertEqual(client.request(credentials, "search", "Київ")[0], RECORD)
        self.assertEqual(Handler.calls[-1][1], "Bearer synthetic-token")
        self.assertEqual(json.loads(Handler.calls[-1][2])["text"], "Київ")
        for suffix in ("/bad", "/denied"):
            with self.assertRaises(ClientError):
                client.request(Credentials(self.url + suffix, "synthetic-token"), "search", "Київ")
        count = len(Handler.calls)
        with self.assertRaises(ClientError):
            client.request(Credentials(self.url + "/redirect", "synthetic-token"), "search", "Київ")
        self.assertEqual(len(Handler.calls), count + 1)
        feedback = QgsFeedback()
        feedback.cancel()
        count = len(Handler.calls)
        with self.assertRaises(ClientError):
            client.request(credentials, "search", "Київ", feedback)
        self.assertEqual(len(Handler.calls), count)

    def test_locator_prefix_short_queries_and_result(self):
        locator = AddressLocatorFilter(Credentials(self.url, "synthetic-token"))
        context = QgsLocatorContext()
        feedback = QgsFeedback()
        received = []
        locator.resultFetched.connect(received.append)
        locator.fetchResults("Київ", context, feedback)
        self.assertEqual(Handler.calls, [])
        context.usingPrefix = True
        locator.fetchResults("Ки", context, feedback)
        self.assertEqual(Handler.calls, [])
        locator.fetchResults("Київ", context, feedback)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].displayString, RECORD.label)
        self.assertEqual(locator.fetchResultsDelay(), 1000)
        self.assertFalse(locator.useWithoutPrefix())
        self.iface.registerLocatorFilter(locator)
        self.assertEqual(locator.activePrefix(), "адр")
        try:
            received.clear()
            self.iface.locator.foundResult.connect(received.append)
            self.iface.locator.fetchResults("адр Київ", QgsLocatorContext())
            self.assertTrue(wait_until(lambda: bool(received)))
            self.assertEqual(received[0].displayString, RECORD.label)
        finally:
            self.iface.deregisterLocatorFilter(locator)

    def test_map_transform_geometry_and_cleanup(self):
        display = MapDisplay(self.iface.canvas)
        for crs in ("EPSG:4326", "EPSG:3857", "EPSG:32636"):
            self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem(crs))
            display.show(RECORD)
            center = display.marker.center()
            x, y = display.wgs84(center)
            self.assertAlmostEqual(x, RECORD.center[0], places=5)
            self.assertAlmostEqual(y, RECORD.center[1], places=5)
            display.clear()
            self.assertIsNone(display.marker)
        data = RECORD.to_dict()
        data["geometry"] = {
            "type": "Polygon",
            "coordinates": [[[30.52, 50.45], [30.53, 50.45], [30.53, 50.46], [30.52, 50.45]]],
        }
        display.show(SearchResult.from_dict(data))
        self.assertIsNotNone(display.band)
        display.clear()
        self.assertIsNone(display.band)

    def test_real_search_reverse_and_stale_reply(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            self.plugin.search("Київ")
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(self.plugin.records, [RECORD])
            self.plugin.select(0)
            self.assertIsNotNone(self.plugin.display.marker)
            self.plugin.activate_reverse()
            self.assertEqual(self.iface.canvas.mapTool(), self.plugin.tool)
            self.plugin.cancel()
            self.assertNotEqual(self.iface.canvas.mapTool(), self.plugin.tool)
            self.iface.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
            self.plugin.reverse_at(QgsPointXY(30.5234, 50.4501))
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertIn("Найближча адреса", self.plugin.dock.status.text())
            self.assertTrue(any(path == "/v1/reverse" for path, _, _ in Handler.calls))
        self.plugin.records = []
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url + "/slow", "synthetic-token")
        ):
            self.plugin.search("Київ")
            self.assertTrue(
                wait_until(lambda: any(p.startswith("/slow/") for p, _, _ in Handler.calls))
            )
            self.plugin.cancel()
            end = time.monotonic() + 0.7
            while time.monotonic() < end:
                events()
                time.sleep(0.01)
            self.assertEqual(self.plugin.records, [])
            self.plugin.search("Київ")
            self.plugin.unload()
            self.assertTrue(
                wait_until(lambda: QgsApplication.taskManager().countActiveTasks() == 0),
                [
                    (task.description(), str(task.status()), task.isCanceled())
                    for task in QgsApplication.taskManager().tasks()
                ],
            )

    def test_panel_locator_geometry_and_project_share_cache(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            with patch.object(
                self.plugin.display, "flash", wraps=self.plugin.display.flash
            ) as flash:
                self.plugin.search("Київ")
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertIsNotNone(self.plugin.display.marker)
                self.assertTrue(flash.called)
            self.plugin.search("  КИЇВ ")
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertIn("З кешу", self.plugin.dock.status.text())
            self.assertEqual(len(Handler.calls), 2)
            results = []
            self.iface.locator.foundResult.connect(results.append)
            self.iface.locator.fetchResults("адр Київ", QgsLocatorContext())
            self.assertTrue(wait_until(lambda: bool(results)))
            self.assertEqual(len(Handler.calls), 2)
            self.assertEqual(Handler.sessions[0], self.plugin.cache.session_id)
            self.plugin.show_geometry(0)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertIsNotNone(self.plugin.display.band)
            self.assertEqual(len(Handler.calls), 2)
            self.plugin.download_selected(0)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            layers = list(QgsProject.instance().mapLayers().values())
            self.assertEqual(len(layers), 1)
            self.assertEqual(layers[0].featureCount(), 1)
            self.assertEqual(layers[0].crs().authid(), "EPSG:4326")
            self.plugin.download_selected(0)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(layers[0].featureCount(), 1)
            self.assertEqual(len(Handler.calls), 2)
            self.plugin.visibility_changed(False)
            self.plugin.search("Київ")
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertIsNotNone(self.plugin.display.band)
            self.assertEqual(len(Handler.calls), 2)
            QgsProject.instance().removeMapLayer(layers[0].id())
            self.plugin.download_selected(0)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(len(Handler.calls), 2)
            self.assertEqual(len(QgsProject.instance().mapLayers()), 1)
            self.assertGreaterEqual(self.plugin.cache.stats["cache"], 4)

    def test_concurrent_requests_share_network_and_cancel_independently(self):
        cache = SessionCache()
        credentials = Credentials(self.url + "/slow", "synthetic-token")
        completed = []
        first = RequestTask(credentials, "search", "Київ", completed.append, cache)
        second = RequestTask(credentials, "search", "КИЇВ", completed.append, cache)
        QgsApplication.taskManager().addTask(first)
        QgsApplication.taskManager().addTask(second)
        self.assertTrue(wait_until(lambda: cache.stats["shared"] == 1))
        first.cancel()
        self.assertTrue(wait_until(lambda: len(completed) == 2))
        self.assertEqual(len(Handler.calls), 1)
        self.assertEqual(second.result, [RECORD])
        self.assertIsNone(second.error)
        self.assertEqual(NetworkClient(cache).request(credentials, "search", "Київ"), [RECORD])
        self.assertEqual(len(Handler.calls), 1)
        cache.close()

    def test_geometry_families_and_custom_prefix_preserved(self):
        for number, (kind, coordinates) in enumerate(
            (
                ("Point", [30, 50]),
                ("LineString", [[30, 50], [31, 51]]),
                ("Polygon", [[[30, 50], [31, 50], [31, 51], [30, 50]]]),
            )
        ):
            data = RECORD.to_dict()
            data["id"] = "family-" + str(number)
            data["geometry"] = {"type": kind, "coordinates": coordinates}
            record = SearchResult.from_dict(data)
            layer, added = download_geometry(record)
            self.assertTrue(added)
            self.assertFalse(download_geometry(record)[1])
            self.assertEqual(layer.featureCount(), 1)
        self.assertEqual(len(QgsProject.instance().mapLayers()), 3)
        for key in LOCATOR_PREFIX_KEYS:
            QgsSettings().setValue(key, "custom")
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.assertEqual(self.plugin.locator.activePrefix(), "custom")

    def test_old_default_prefix_migrates_without_setting_password(self):
        for key in LOCATOR_PREFIX_KEYS:
            QgsSettings().setValue(key, "uas")
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.assertEqual(self.plugin.locator.activePrefix(), "адр")
        for key in LOCATOR_PREFIX_KEYS:
            self.assertEqual(QgsSettings().value(key), "адр")

    def test_repeated_pending_panel_query_does_not_cancel_its_request(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url + "/slow", "synthetic-token")
        ):
            self.plugin.search("Київ")
            self.assertTrue(wait_until(lambda: bool(Handler.calls)))
            self.plugin.search("  КИЇВ ")
            self.assertEqual(len(self.plugin.tasks), 1)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(len(Handler.calls), 2)
            self.assertEqual(self.plugin.records, [RECORD])

    def test_geometry_checkbox_copy_and_compact_actions(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        dock = self.plugin.dock
        self.assertTrue(dock.auto_geometry.isChecked())
        self.assertFalse(hasattr(dock, "quota_button"))
        for button in (
            dock.download_button,
            dock.reverse_button,
            dock.copy_button,
            dock.cancel_button,
            dock.settings_button,
        ):
            self.assertFalse(button.icon().isNull())
            self.assertTrue(button.toolTip())
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            dock.auto_geometry.setChecked(False)
            self.plugin.search("Київ")
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(len(Handler.calls), 1)
            self.assertIsNotNone(self.plugin.display.marker)
            self.assertIsNone(self.plugin.display.band)
            dock.copy_button.click()
            self.assertEqual(QApplication.clipboard().text(), RECORD.label)
            self.assertIn("99", dock.quota.text())
            dock.auto_geometry.setChecked(True)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertEqual(len(Handler.calls), 2)
            self.assertIsNotNone(self.plugin.display.band)
            dock.auto_geometry.setChecked(False)
            self.assertIsNone(self.plugin.display.band)
            self.assertIsNotNone(self.plugin.display.marker)
            dock.auto_geometry.setChecked(True)
            self.assertEqual(len(Handler.calls), 2)
            self.assertIsNotNone(self.plugin.display.band)
            self.assertTrue(Settings().auto_geometry())
            self.plugin.cancel()
            self.assertNotIn("скасовано", dock.status.text())

    def test_anonymous_enrollment_and_existing_token_migration(self):
        manager = QgsApplication.authManager()
        self.assertTrue(manager.setMasterPassword("uaas-test-master-password", True))
        settings = Settings()
        settings.values.setValue(PREFIX + "server_url", self.url)
        try:
            first = settings.credentials()
            second = Settings().credentials()
            self.assertEqual(first, second)
            uuid.UUID(first.installation_id)
            self.assertGreaterEqual(len(first.token), 43)
            self.assertNotIn(first.token, repr(first))
            cache = SessionCache()
            client = NetworkClient(cache)
            client.request(first, "search", "Київ")
            client.request(first, "feature", RECORD.id)
            client.request(first, "search", " КИЇВ ")
            self.assertEqual(
                [p for p, _, _ in Handler.calls],
                ["/v1/register", "/v1/search", "/v1/features/ADR_TEST"],
            )
            self.assertEqual(cache.quota["remaining"], 97)
            cache.close()
        finally:
            identifier = settings.values.value(PREFIX + "authcfg", "")
            if identifier:
                manager.removeAuthenticationConfig(identifier)
            settings.values.remove(PREFIX)
        settings.save(self.url, "existing-private-token")
        try:
            self.assertEqual(settings.credentials().installation_id, "")
            self.assertEqual(settings.credentials().token, "existing-private-token")
            settings.save_mode("visicom", "own-private-key", 8, False)
            direct = settings.credentials()
            self.assertEqual(
                (direct.mode, direct.token, direct.quota_limit, direct.show_quota),
                ("visicom", "own-private-key", 8, False),
            )
            settings.save_mode("railway")
            self.assertEqual(settings.credentials().token, "existing-private-token")
        finally:
            for key in ("authcfg", "visicom_authcfg"):
                manager.removeAuthenticationConfig(settings.values.value(PREFIX + key, ""))

    def test_direct_provider_cache_and_local_quota_without_railway(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = Credentials(
                self.url + "/direct",
                "private-visicom-key",
                "visicom",
                quota_limit=1,
                usage_path=str(Path(directory) / "usage.sqlite"),
            )
            cache = SessionCache()
            client = NetworkClient(cache)
            results = client.request(credentials, "search", "Київ")
            client.request(credentials, "search", " КИЇВ ")
            self.assertEqual(len(Handler.calls), 1)
            self.assertEqual(cache.quota["used"], 1)
            client.request(credentials, "feature", results[0].id)
            self.assertEqual(cache.quota["used"], 2)
            self.assertEqual(cache.quota["exceeded"], 1)
            client.request(credentials, "reverse", (30, 50))
            self.assertEqual(len(Handler.calls), 3)
            self.assertTrue(
                all(path.startswith("/direct/") and auth is None for path, auth, _ in Handler.calls)
            )
            self.assertTrue(all(s is None for s in Handler.sessions))
            self.assertEqual(cache.quota["used"], 3)
            cache.close()

    def test_selection_and_checkbox_reject_stale_geometry(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            self.plugin.refresh_credentials()
            self.plugin.records = [replace(RECORD, id="ADR_SLOW"), replace(RECORD, id="ADR_FAST")]
            self.plugin.dock.show_results(self.plugin.records)
            self.plugin.dock.results.setCurrentRow(0)
            self.assertTrue(wait_until(lambda: bool(Handler.calls)))
            self.plugin.dock.results.setCurrentRow(1)
            self.assertTrue(wait_until(lambda: not self.plugin.tasks))
            self.assertIsNotNone(self.plugin.display.band)
            self.assertEqual(self.plugin.dock.results.currentRow(), 1)
            self.plugin.dock.auto_geometry.setChecked(False)
            self.plugin.dock.results.setCurrentRow(0)
            self.plugin.dock.auto_geometry.setChecked(True)
            self.assertTrue(wait_until(lambda: bool(self.plugin.tasks)))
            self.plugin.dock.auto_geometry.setChecked(False)
            self.assertIsNone(self.plugin.display.band)
            self.assertEqual(self.plugin.tasks, set())
            self.assertTrue(
                wait_until(lambda: QgsApplication.taskManager().countActiveTasks() == 0),
                [
                    (task.description(), str(task.status()), task.isCanceled())
                    for task in QgsApplication.taskManager().tasks()
                ],
            )
            self.assertIsNone(self.plugin.display.band)

    def test_cached_repeat_preserves_pending_geometry(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        slow = replace(RECORD, id="ADR_HOLD")
        Handler.geometry_gate = threading.Event()
        original = NetworkClient._request

        def request(client, credentials, operation, value=None, feedback=None):
            records = original(client, credentials, operation, value, feedback)
            return [slow] if operation == "search" else records

        with patch.object(
            Settings, "credentials", return_value=Credentials(self.url, "synthetic-token")
        ):
            with patch.object(NetworkClient, "_request", request):
                try:
                    self.plugin.search("Київ")
                    self.assertTrue(
                        wait_until(Handler.geometry_started.is_set),
                        (Handler.calls, self.plugin.dock.status.text()),
                    )
                    self.plugin.search(" КИЇВ ")
                    self.assertTrue(
                        wait_until(
                            lambda: not any(t.operation == "search" for t in self.plugin.tasks)
                        )
                    )
                finally:
                    Handler.geometry_gate.set()
                self.assertTrue(wait_until(lambda: not self.plugin.tasks))
                self.assertEqual(len(Handler.calls), 2)
                self.assertIsNotNone(self.plugin.display.band)

    def test_cancel_during_reply_creation_and_timeout(self):
        manager = QgsNetworkAccessManager.instance()
        original_get = manager.get
        feedback = QgsFeedback()

        def get(request):
            reply = original_get(request)
            feedback.cancel()
            return reply

        started = time.monotonic()
        with patch.object(manager, "get", get), self.assertRaises(ClientError):
            NetworkClient().request(
                Credentials(self.url, "synthetic-token"), "feature", RECORD.id, feedback
            )
        self.assertLess(time.monotonic() - started, 2)
        with patch("ua_address_search.core.client.REQUEST_TIMEOUT_MS", 30):
            with self.assertRaises(ClientError):
                NetworkClient().request(
                    Credentials(self.url + "/slow", "synthetic-token"), "search", "Київ"
                )
