"""WGS84 transforms and temporary map overlays."""

import json
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker


class ReverseMapTool(QgsMapToolEmitPoint):
    coordinates_selected = pyqtSignal(object)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvasClicked.connect(self.clicked)

    def clicked(self, point, button):
        left = getattr(Qt, "LeftButton", None)
        if left is None:
            left = Qt.MouseButton.LeftButton
        if button == left:
            self.coordinates_selected.emit(point)


class MapDisplay:
    def __init__(self, canvas):
        self.canvas = canvas
        self.marker = None
        self.band = None

    def transform(self, source, target):
        return QgsCoordinateTransform(source, target, QgsProject.instance().transformContext())

    def wgs84(self, map_point):
        crs = self.canvas.mapSettings().destinationCrs()
        result = self.transform(crs, QgsCoordinateReferenceSystem("EPSG:4326")).transform(map_point)
        return result.x(), result.y()

    def show(self, result):
        self.clear()
        transform = self.transform(
            QgsCoordinateReferenceSystem("EPSG:4326"), self.canvas.mapSettings().destinationCrs()
        )
        center = transform.transform(QgsPointXY(*result.center))
        marker = QgsVertexMarker(self.canvas)
        marker.setIconType(QgsVertexMarker.ICON_CROSS)
        marker.setIconSize(18)
        marker.setPenWidth(3)
        marker.setColor(QColor("#1673a5"))
        marker.setCenter(center)
        self.marker = marker
        box = result.bbox
        if not box or box[0] == box[2] or box[1] == box[3]:
            x, y = result.center
            box = (
                max(-180, x - 0.001),
                max(-90, y - 0.001),
                min(180, x + 0.001),
                min(90, y + 0.001),
            )
        extent = transform.transformBoundingBox(QgsRectangle(*box))
        extent.scale(1.3)
        self.canvas.setExtent(extent)
        if result.geometry:
            feature = {"type": "Feature", "properties": {}, "geometry": result.geometry}
            # QgsJsonUtils is available throughout QGIS 3.28–4.x.
            from qgis.core import QgsJsonUtils

            features = QgsJsonUtils.stringToFeatureList(json.dumps(feature))
            if not features or features[0].geometry().isNull():
                raise ValueError("Не вдалося відобразити геометрію об’єкта.")
            geom = QgsGeometry(features[0].geometry())
            geom.transform(transform)
            self.band = QgsRubberBand(self.canvas, geom.type())
            self.band.setColor(QColor(22, 115, 165, 90))
            self.band.setWidth(3)
            self.band.setToGeometry(geom)
        self.canvas.refresh()

    def clear(self):
        for item in (self.marker, self.band):
            if item is not None:
                self.canvas.scene().removeItem(item)
        self.marker = None
        self.band = None
