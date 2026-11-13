"""Task lifecycle for the five additional function pages."""

import json
from qgis.PyQt import sip
from qgis.core import (
    QgsCsException,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsPointXY,
)
from .core.client import RequestTask
from .core.operations import validate_request
from .core.qgis_io import memory_outputs, result_records
from .core.qgis_io import make_feature, output_fields
from .core.models import SearchResult
from .core.settings import PREFIX
from .map_tools import ReverseMapTool
from .ui.function_tabs import OPERATIONS


class FunctionController:
    def __init__(self, plugin):
        self.plugin = plugin
        self.tasks = set()
        self.generation = 0
        self.pick_editor = None
        self.previous_tool = None
        self.tool = ReverseMapTool(plugin.iface.mapCanvas())
        self.tool.coordinates_selected.connect(self.picked)
        self.pages = plugin.dock.pages
        for page in self.pages.values():
            page.execute_requested.connect(self.execute)
            page.cancel_requested.connect(self.cancel)
            page.export_requested.connect(self.export)
            page.pick_requested.connect(self.activate_pick)
            page.results.currentCellChanged.connect(
                lambda row, column, old_row, old_column, tab=page.operation: self.preview(tab, row)
            )
        self.pages["optimize"].route_requested.connect(self.optimized_route)
        plugin.dock.tabs.currentChanged.connect(self.tab_changed)
        index = plugin.settings.values.value(PREFIX + "active_tab", 0, type=int)
        plugin.dock.tabs.setCurrentIndex(max(0, min(5, index)))

    def tab_changed(self, index):
        self.plugin.cancel(silent=True)
        self.plugin.restore_tool()
        self.plugin.settings.values.setValue(PREFIX + "active_tab", index)

    def restore_tool(self):
        canvas = self.plugin.iface.mapCanvas()
        previous = self.previous_tool
        self.previous_tool = None
        self.pick_editor = None
        if canvas.mapTool() != self.tool:
            return
        if previous is None or sip.isdeleted(previous):
            canvas.unsetMapTool(self.tool)
        else:
            try:
                canvas.setMapTool(previous)
            except RuntimeError:
                if not sip.isdeleted(previous):
                    raise
                if canvas.mapTool() == self.tool:
                    canvas.unsetMapTool(self.tool)

    def activate_pick(self, editor):
        self.plugin.restore_tool()
        canvas = self.plugin.iface.mapCanvas()
        if canvas.mapTool() != self.tool:
            self.previous_tool = canvas.mapTool()
            canvas.setMapTool(self.tool)
        self.pick_editor = editor

    def picked(self, position):
        if self.pick_editor is None:
            return
        try:
            coords = self.plugin.display.wgs84(QgsPointXY(position))
            self.pick_editor.append(coords)
        except (ValueError, RuntimeError, QgsCsException):
            page = self.pages[OPERATIONS[self.plugin.dock.tabs.currentIndex()]]
            page.status.setText("Не вдалося перетворити координати кліку у WGS84.")

    def cancel(self):
        self.generation += 1
        for task in self.tasks:
            task.callback = None
            task.cancel()
        self.tasks.clear()
        for page in self.pages.values():
            if page.cancel_button.isEnabled():
                page.status.setText("Запит скасовано; попередній результат збережено.")
            page.cancel_button.setEnabled(False)
            page.execute_button.setEnabled(True)
        self.restore_tool()

    def search_area(self, page):
        canvas = self.plugin.iface.mapCanvas()
        if page.scope.currentIndex() == 2:
            geom = QgsGeometry.fromRect(canvas.extent())
            crs = canvas.mapSettings().destinationCrs()
        else:
            layer = self.plugin.iface.activeLayer()
            if not layer or not hasattr(layer, "selectedFeatures"):
                raise ValueError("Виберіть один полігон в активному векторному шарі.")
            selected = layer.selectedFeatures()
            if len(selected) != 1:
                raise ValueError("Потрібен рівно один виділений полігон.")
            geom = QgsGeometry(selected[0].geometry())
            crs = layer.crs()
        if not crs.isValid() or geom.isNull() or not geom.isGeosValid():
            raise ValueError("Область має мати відомий CRS і коректну геометрію.")
        transform = QgsCoordinateTransform(
            crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()
        )
        geom.transform(transform)
        return json.loads(geom.asJson())

    def execute(self, tab, override=None):
        page = self.pages[tab]
        try:
            operation, value, ids = override or page.parameters()
            if operation == "objects" and page.scope.currentIndex() in (2, 3):
                value["area"] = self.search_area(page)
            value = validate_request(operation, value)
            credentials = self.plugin.refresh_credentials()
            if not credentials.token:
                raise ValueError("Налаштуйте доступ до API.")
        except (ValueError, RuntimeError, QgsCsException) as exc:
            page.status.setText(str(exc))
            return
        self.cancel()
        generation = self.generation
        page.execute_button.setEnabled(False)
        page.cancel_button.setEnabled(True)
        page.status.setText("Виконується запит…")

        def completed(task):
            self.tasks.discard(task)
            if generation != self.generation or not self.plugin.active:
                return
            page.execute_button.setEnabled(True)
            page.cancel_button.setEnabled(False)
            self.plugin.update_quota()
            if task.error:
                page.status.setText(task.error + " Попередній результат збережено.")
                return
            page.last_result, page.last_request, page.last_ids = task.result, value, ids
            page.show_records(result_records(operation, task.result, value, ids))
            if page.output_records:
                page.results.setCurrentCell(0, 0)
            status = f"Результатів: {len(page.output_records)}. Дані: Visicom."
            if operation == "objects" and len(task.result) >= value["limit"]:
                status += " Досягнуто ліміту; вибірка може бути неповною."
            if operation == "route" and task.result["status"] != "OK":
                status = "Маршрут не знайдено."
            if tab == "optimize":
                page.route_button.setEnabled(operation == "optimize")
            if task.source != "network":
                status += " З кешу сесії."
            page.status.setText(status)

        task = RequestTask(credentials, operation, value, completed, self.plugin.cache)
        self.tasks.add(task)
        QgsApplication.taskManager().addTask(task)

    def preview(self, tab, row):
        page = self.pages[tab]
        if not 0 <= row < len(page.output_records):
            return
        record = page.output_records[row]
        if not record.get("geometry"):
            return
        try:
            geom = make_feature(record, output_fields()).geometry()
            box = geom.boundingBox()
            center = box.center()
            result = SearchResult(
                str(row),
                str(record["attributes"].get("label") or page.title),
                "",
                tab,
                (center.x(), center.y()),
                (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
                geometry=record["geometry"],
            )
            self.plugin.display.show(result)
        except (ValueError, RuntimeError, QgsCsException):
            page.status.setText("Не вдалося відобразити геометрію результату.")

    def optimized_route(self):
        page = self.pages["optimize"]
        if not page.last_result or "order" not in page.last_result:
            return
        points = [page.last_request["points"][i] for i in page.last_result["order"]]
        if page.last_request["round_trip"]:
            points.append(points[0])
        value = {
            "points": points,
            "mode": page.last_request["mode"],
            "locks": page.last_request["locks"],
        }
        try:
            validate_request("route", value)
        except ValueError as exc:
            page.status.setText(str(exc))
            return
        route_page = self.pages["route"]
        route_page.mode.setCurrentIndex(route_page.mode.findData(value["mode"]))
        for key in ("points", "locks"):
            editor = route_page.editors[key]
            editor.table.setRowCount(0)
            for coordinates in value[key]:
                editor.append(coordinates)
        # Keep the optimization's numbered points intact while the Route page owns the line.
        self.plugin.dock.tabs.setCurrentIndex(2)
        self.execute("route")

    def export(self, tab):
        page = self.pages[tab]
        try:
            layers = memory_outputs(page.output_records, "Visicom — " + page.title)
            for layer in layers:
                QgsProject.instance().addMapLayer(layer)
            page.status.setText(f"Додано шарів: {len(layers)}. Дані: Visicom.")
        except ValueError as exc:
            page.status.setText(str(exc))

    def unload(self):
        self.cancel()
        self.tool.coordinates_selected.disconnect(self.picked)
        self.tool.deleteLater()
