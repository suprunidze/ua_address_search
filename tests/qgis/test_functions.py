"""Real QGIS/Qt integration, with synthetic transport and isolated credentials."""

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from qgis.PyQt import sip
from qgis.PyQt.QtNetwork import QNetworkReply
from qgis.PyQt.QtGui import QFont, QFontDatabase
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsMapToolPan
from visicom_data_api import classFactory
from visicom_data_api.core.client import ClientError, NetworkClient
from visicom_data_api.core.models import SearchResult
from visicom_data_api.core.operations import normalize_response
from visicom_data_api.core.qgis_io import memory_outputs, source_points
from visicom_data_api.core.settings import Credentials, PREFIX, Settings, VISICOM_URL
from visicom_data_api.processing_provider import VisicomAlgorithm
from test_plugin import Iface, events, wait_until

P = [30.5, 50.4]
FEATURE = {
    "type": "Feature",
    "id": "TEST_1",
    "geo_centroid": {"type": "Point", "coordinates": P},
    "properties": {"name": "Synthetic"},
}
ROUTE = {
    "type": "Feature",
    "geometry": {"type": "LineString", "coordinates": [P, P]},
    "properties": {"distance": 123},
}


def layer(count=2, crs="EPSG:4326", duplicate=False):
    result = QgsVectorLayer(
        "Point?crs=" + crs + "&field=seq:integer&field=text:string", "Synthetic", "memory"
    )
    features = []
    for index in range(count):
        feature = QgsFeature(result.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*P)))
        feature.setAttributes([0 if duplicate else count - index, "Адреса тестова"])
        features.append(feature)
    result.dataProvider().addFeatures(features)
    QgsProject.instance().addMapLayer(result)
    return result


class FunctionTests(unittest.TestCase):
    def setUp(self):
        QgsSettings().remove(PREFIX)
        self.iface = Iface()
        self.plugin = None
        self.contexts = []
        self.temp = tempfile.TemporaryDirectory()
        self.creds = Credentials(
            VISICOM_URL,
            "synthetic-own-key",
            "visicom",
            usage_path=str(Path(self.temp.name) / "usage.sqlite"),
        )

    def tearDown(self):
        if self.plugin:
            self.plugin.unload()
        QgsProject.instance().removeAllMapLayers()
        self.iface.close()
        QgsSettings().remove(PREFIX)
        events()
        self.contexts.clear()
        self.temp.cleanup()

    def plugin_init(self):
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        return self.plugin

    def algorithm(self, operation):
        algorithm = VisicomAlgorithm(operation, operation)
        algorithm.initAlgorithm()
        return algorithm

    def run_algorithm(self, operation, parameters, response, fail_after=None):
        algorithm = self.algorithm(operation)
        for name in ("POINTS", "LINES", "POLYGONS", "TABLE"):
            parameters.setdefault(name, QgsProcessing.TEMPORARY_OUTPUT)
        context = QgsProcessingContext()
        self.contexts.append(context)
        context.setProject(QgsProject.instance())
        calls = []

        def transport(request, body, feedback):
            self.assertIsNone(body)
            self.assertFalse(bytes(request.rawHeader(b"Authorization")))
            self.assertIn("synthetic-own-key", request.url().query())
            calls.append(request.url().path())
            if fail_after is not None and len(calls) > fail_after:
                raise ClientError("Синтетична помилка.")
            payload = response(request) if callable(response) else response
            return 200, json.dumps(payload).encode(), QNetworkReply.NetworkError.NoError

        with patch.object(Settings, "credentials", return_value=self.creds) as credentials:
            with patch.object(NetworkClient, "_send", side_effect=transport):
                outputs, ok = algorithm.run(parameters, context, QgsProcessingFeedback())
            credentials.assert_called_once_with(own_key=True)
        self.assertTrue(ok, outputs)
        layers = {
            name: context.getMapLayer(outputs[name])
            for name in ("POINTS", "LINES", "POLYGONS", "TABLE")
        }
        return outputs, layers, calls

    def test_provider_tabs_footer_help_and_persistence(self):
        plugin = self.plugin_init()
        provider = QgsApplication.processingRegistry().providerById("visicom_data_api")
        self.assertEqual(len(provider.algorithms()), 8)
        self.assertEqual(plugin.dock.tabs.count(), 6)
        self.assertEqual(
            [plugin.dock.footer.itemAt(i).widget() for i in range(3)],
            [plugin.dock.quota, plugin.dock.settings_button, plugin.dock.help_button],
        )
        plugin.dock.tabs.setCurrentIndex(3)
        with patch("visicom_data_api.plugin.QDesktopServices.openUrl", return_value=True) as opened:
            plugin.show_help()
            self.assertEqual(opened.call_args[0][0].fragment(), "matrix")
        plugin.unload()
        self.assertIsNone(QgsApplication.processingRegistry().providerById("visicom_data_api"))
        plugin.initGui()
        self.assertEqual(plugin.dock.tabs.currentIndex(), 3)
        self.assertEqual(
            len(QgsApplication.processingRegistry().providerById("visicom_data_api").algorithms()),
            8,
        )
        plugin.settings.values.setValue(PREFIX + "mode", "visicom")
        NetworkClient._local_quota(self.creds, True)
        with patch.object(Settings, "credentials", return_value=self.creds):
            plugin.processing_usage_changed()
        self.assertEqual(plugin.cache.identity, self.creds.identity)
        self.assertEqual(plugin.cache.quota["used"], 1)

    def test_switch_cancels_tasks_and_deleted_pick_tool_is_safe(self):
        plugin = self.plugin_init()
        page = plugin.dock.pages["route"]
        plugin.dock.tabs.setCurrentIndex(2)
        previous = QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        plugin.functions.activate_pick(page.editors["points"])
        sip.delete(previous)
        plugin.dock.tabs.setCurrentIndex(1)
        self.assertIsNone(plugin.functions.previous_tool)
        self.assertNotEqual(self.iface.canvas.mapTool(), plugin.functions.tool)
        page.editors["points"].append(P)
        page.editors["points"].append(P)
        with patch.object(Settings, "credentials", return_value=self.creds):
            with patch.object(
                NetworkClient,
                "request",
                return_value=normalize_response("route", ROUTE, {"points": [P, P]}),
            ):
                plugin.dock.tabs.setCurrentIndex(2)
                plugin.functions.execute("route")
                plugin.dock.tabs.setCurrentIndex(1)
                self.assertTrue(wait_until(lambda: not plugin.functions.tasks))
                self.assertIsNone(page.last_result)
        self.assertEqual(page.editors["points"].table.rowCount(), 2)

    def test_dock_request_results_export_and_preflight(self):
        plugin = self.plugin_init()
        plugin.dock.tabs.setCurrentIndex(2)
        page = plugin.dock.pages["route"]
        for _ in range(28):
            page.editors["points"].append(P)
        with patch.object(Settings, "credentials") as credentials:
            plugin.functions.execute("route")
            credentials.assert_not_called()
            self.assertIn("28", page.status.text())
        page.editors["points"].table.setRowCount(2)
        with patch.object(Settings, "credentials", return_value=self.creds):
            with patch.object(
                NetworkClient,
                "_send",
                return_value=(200, json.dumps(ROUTE).encode(), QNetworkReply.NetworkError.NoError),
            ):
                plugin.functions.execute("route")
                self.assertTrue(wait_until(lambda: not plugin.functions.tasks))
        self.assertEqual(page.last_result["distance_m"], 123)
        plugin.functions.export("route")
        output = list(QgsProject.instance().mapLayers().values())
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0].featureCount(), 1)
        self.assertEqual(output[0].crs().authid(), "EPSG:4326")

    def test_processing_own_key_independent_and_missing_key_zero_requests(self):
        self.assertTrue(
            QgsApplication.authManager().setMasterPassword("uaas-test-master-password", True)
        )
        settings = Settings()
        settings.save_mode("visicom", "synthetic-personal-key")
        settings.save_mode("railway")
        own = settings.credentials(own_key=True)
        self.assertEqual(own.mode, "visicom")
        self.assertEqual(own.token, "synthetic-personal-key")
        self.assertEqual(settings.mode(), "railway")
        QgsSettings().remove(PREFIX)
        with patch.object(NetworkClient, "request") as network:
            with self.assertRaises(Exception) as error:
                self.algorithm("route").prepareAlgorithm(
                    {}, QgsProcessingContext(), QgsProcessingFeedback()
                )
            self.assertIn("власного ключа", str(error.exception))
            network.assert_not_called()

    def test_source_order_crs_and_invalid_inputs(self):
        source = layer()
        records = source_points(source, "seq")
        self.assertEqual(records[0]["source_id"], "2")
        self.assertEqual(records[0]["point"], P)
        duplicate = layer(duplicate=True)
        with self.assertRaises(ValueError):
            source_points(duplicate, "seq")
        source.setCrs(QgsCoordinateReferenceSystem())
        with self.assertRaises(ValueError):
            source_points(source)

    def test_processing_all_output_families_and_matrix_status(self):
        source = layer()
        outputs, layers, calls = self.run_algorithm(
            "route", {"INPUT": source, "ORDER": "seq"}, ROUTE
        )
        self.assertEqual(outputs["STATUS"], "completed")
        self.assertEqual(layers["LINES"].featureCount(), 1)
        route = next(layers["LINES"].getFeatures())
        self.assertEqual(json.loads(route["source_ids"]), ["2", "1"])
        self.assertEqual(calls, ["/data-api/5.0/core/distance.json"])
        outputs, layers, calls = self.run_algorithm(
            "matrix",
            {"ORIGINS": source, "DESTINATIONS": source},
            {
                "rows": [
                    [{"status": "OK", "distance": 0}, {"status": "NOT_FOUND"}],
                    [{"status": "OK", "distance": 10}, {"status": "OK", "distance": 0}],
                ]
            },
        )
        self.assertEqual(layers["TABLE"].featureCount(), 4)
        self.assertEqual(len(calls), 1)
        outputs, layers, _ = self.run_algorithm(
            "optimize", {"INPUT": source, "ORDER": "seq"}, {"list": [{"index": 0}, {"index": 1}]}
        )
        self.assertEqual(layers["POINTS"].featureCount(), 2)
        outputs, layers, _ = self.run_algorithm(
            "snap",
            {"INPUT": source, "ORDER": "seq", "INTERPOLATE": True},
            {"type": "Feature", "geometry": ROUTE["geometry"]},
        )
        self.assertEqual(layers["LINES"].featureCount(), 1)
        for operation in ("geocode", "reverse"):
            outputs, layers, calls = self.run_algorithm(
                operation, {"INPUT": source, "FIELD": "text"}, FEATURE
            )
            self.assertEqual(layers["POINTS"].featureCount(), 2)
            # Identical forward addresses share the run cache; reverse points do too.
            self.assertEqual(len(calls), 1)

    def test_processing_partial_error_and_no_matches(self):
        source = layer()
        outputs, layers, calls = self.run_algorithm(
            "reverse", {"INPUT": source}, {"type": "FeatureCollection", "features": []}
        )
        self.assertEqual(layers["TABLE"].featureCount(), 2)
        self.assertEqual(outputs["STATUS"], "completed")
        # Different addresses prevent the second job from using the first cache entry.
        source.startEditing()
        source.changeAttributeValue(2, 1, "Інша адреса")
        source.commitChanges()
        outputs, layers, calls = self.run_algorithm(
            "geocode", {"INPUT": source, "FIELD": "text"}, FEATURE, fail_after=1
        )
        self.assertEqual(outputs["STATUS"], "partial_error")
        self.assertEqual(layers["POINTS"].featureCount(), 1)

    def test_mixed_objects_output_and_input_references(self):
        point_record = SearchResult.from_dict({"id": "P", "center": P}).to_dict()
        records = [
            {"geometry": {"type": "Point", "coordinates": P}, "attributes": {"id": "P"}},
            {"geometry": ROUTE["geometry"], "attributes": {"id": "L"}},
            {
                "geometry": {"type": "Polygon", "coordinates": [[P, [31, 50], [31, 51], P]]},
                "attributes": {"id": "A"},
            },
        ]
        self.assertEqual(len(memory_outputs(records, "Synthetic")), 3)
        source = layer(1)
        outputs, layers, _ = self.run_algorithm(
            "features",
            {"INPUT": source, "FIELD": "seq"},
            {"type": "FeatureCollection", "features": []},
        )
        self.assertEqual(layers["TABLE"].featureCount(), 1)
        self.assertEqual(next(layers["TABLE"].getFeatures())["source_id"], "1")
        self.assertEqual(point_record["id"], "P")

    def test_client_preflight_never_registers_or_sends(self):
        with patch.object(NetworkClient, "_send") as transport:
            for op, data in (
                ("route", {"points": [P] * 28}),
                ("matrix", {"origins": [P] * 26, "destinations": [P]}),
                ("optimize", {"points": [P] * 51}),
                ("snap", {"points": [P] * 251}),
            ):
                with self.assertRaises(ClientError):
                    NetworkClient().request(replace(self.creds, mode="railway"), op, data)
            transport.assert_not_called()

    def test_processing_limit_checks_no_requests_and_file_sink(self):
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        with patch.object(NetworkClient, "request") as network:
            for operation, size in (("route", 28), ("optimize", 51), ("snap", 251)):
                source = layer(size)
                algorithm = self.algorithm(operation)
                algorithm.credentials = self.creds
                with self.assertRaises(Exception) as error:
                    algorithm.processAlgorithm(
                        {"INPUT": source, "ORDER": "seq"}, context, QgsProcessingFeedback()
                    )
                self.assertIn(str(size), str(error.exception))
            source = layer(26)
            algorithm = self.algorithm("matrix")
            algorithm.credentials = self.creds
            with self.assertRaises(Exception):
                algorithm.processAlgorithm(
                    {"ORIGINS": source, "DESTINATIONS": source}, context, QgsProcessingFeedback()
                )
            network.assert_not_called()
        source = layer()
        # OGR keeps pooled GeoPackage handles until exitQgis on Windows. The runner
        # removes its isolated directory after exitQgis, unlike per-test cleanup.
        destination = str(Path(os.environ["QGIS_AUTH_DB_DIR_PATH"]) / "test-route.gpkg")
        outputs, _, calls = self.run_algorithm(
            "route", {"INPUT": source, "ORDER": "seq", "LINES": destination}, ROUTE
        )
        stored = QgsVectorLayer(outputs["LINES"], "stored", "ogr")
        self.assertTrue(stored.isValid())
        self.assertEqual(stored.featureCount(), 1)
        self.assertEqual(stored.crs().authid(), "EPSG:4326")
        self.assertEqual(len(calls), 1)

    def test_processing_object_search_area_and_mixed_geometry(self):
        polygon = dict(
            FEATURE,
            id="POLYGON",
            geometry={"type": "Polygon", "coordinates": [[P, [30.6, 50.4], [30.6, 50.5], P]]},
        )
        line = dict(FEATURE, id="LINE", geometry=ROUTE["geometry"])
        collection = {"type": "FeatureCollection", "features": [FEATURE, line, polygon]}
        outputs, layers, calls = self.run_algorithm(
            "objects",
            {"CATEGORIES": "adr_address", "SCOPE": 2, "EXTENT": "30,31,50,51 [EPSG:4326]"},
            collection,
        )
        for name in ("POINTS", "LINES", "POLYGONS"):
            self.assertEqual(layers[name].featureCount(), 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(outputs["STATUS"], "completed")

    def test_processing_cancellation_preserves_completed_records(self):
        source = layer()
        algorithm = self.algorithm("reverse")
        algorithm.credentials = self.creds
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        feedback = QgsProcessingFeedback()
        parameters = {"INPUT": source}
        parameters.update(
            {
                name: QgsProcessing.TEMPORARY_OUTPUT
                for name in ("POINTS", "LINES", "POLYGONS", "TABLE")
            }
        )

        def request(*args):
            feedback.cancel()
            return [SearchResult.from_dict({"id": "SYNTHETIC", "center": P})]

        with patch.object(NetworkClient, "request", side_effect=request) as network:
            outputs = algorithm.processAlgorithm(parameters, context, feedback)
        self.assertEqual(outputs["STATUS"], "partial_canceled")
        self.assertEqual(context.getMapLayer(outputs["POINTS"]).featureCount(), 1)
        self.assertEqual(network.call_count, 1)

    def test_panel_visual_layout_and_optimization_keeps_order(self):
        plugin = self.plugin_init()
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/arial.ttf"
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            plugin.dock.setFont(QFont("Arial", 10))
        plugin.dock.resize(560, 850)
        plugin.dock.show()
        plugin.dock.tabs.setCurrentIndex(2)
        events()
        destination = Path(__file__).resolve().parents[2] / "artifacts"
        destination.mkdir(exist_ok=True)
        self.assertTrue(plugin.dock.grab().save(str(destination / "function-panel.png")))
        editor = plugin.dock.pages["route"].editors["points"]
        for _ in range(3):
            editor.append(P)
        editor.table.setCurrentCell(1, 0)
        editor.remove()
        editor.append(P)
        self.assertEqual(len({record["source_id"] for record in editor.records()}), 3)
        page = plugin.dock.pages["optimize"]
        page.last_request = {"points": [P] * 27, "round_trip": True, "mode": "driving", "locks": []}
        page.last_result = {"order": list(range(27))}
        with patch.object(NetworkClient, "request") as network:
            plugin.functions.optimized_route()
            network.assert_not_called()
            self.assertIn("28", page.status.text())
        page.last_request["points"] = [P] * 2
        page.last_result = {"order": [0, 1]}
        with patch.object(Settings, "credentials", return_value=self.creds):
            with patch.object(
                NetworkClient,
                "_send",
                return_value=(200, json.dumps(ROUTE).encode(), QNetworkReply.NetworkError.NoError),
            ):
                plugin.functions.optimized_route()
                self.assertTrue(wait_until(lambda: not plugin.functions.tasks))
        self.assertEqual(page.last_result["order"], [0, 1])
        self.assertEqual(plugin.dock.tabs.currentIndex(), 2)
        self.assertEqual(plugin.dock.pages["route"].last_result["distance_m"], 123)
