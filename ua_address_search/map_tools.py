"""WGS84 transforms and temporary map overlays."""

import json
from qgis.PyQt.QtCore import Qt, pyqtSignal, QVariant, QVariantAnimation
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsFeature,
    QgsField,
    QgsVectorLayer,
    QgsWkbTypes,
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
    def __init__(self, canvas, on_change=None):
        self.canvas = canvas
        self.marker = None
        self.band = None
        self.flashes = {}
        self.on_change = on_change

    def has_overlays(self):
        return self.marker is not None or self.band is not None or bool(self.flashes)

    def changed(self):
        if self.on_change is not None:
            self.on_change()

    def transform(self, source, target):
        return QgsCoordinateTransform(source, target, QgsProject.instance().transformContext())

    def wgs84(self, map_point):
        crs = self.canvas.mapSettings().destinationCrs()
        result = self.transform(crs, QgsCoordinateReferenceSystem("EPSG:4326")).transform(map_point)
        return result.x(), result.y()

    def show(self, result, recenter=True):
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
        if recenter:
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
        self.flash(QgsGeometry.fromPointXY(center))
        if result.geometry:
            self.flash(geom)
        self.canvas.refresh()
        self.changed()

    def flash(self, geometry):
        """Keep ownership of every animation and overlay so cancellation is immediate."""
        band = QgsRubberBand(self.canvas, geometry.type())
        band.setWidth(2)
        band.setToGeometry(geometry)
        animation = QVariantAnimation(self.canvas)
        animation.setDuration(500)
        animation.setLoopCount(3)
        animation.setStartValue(QColor(255, 0, 0, 0))
        animation.setKeyValueAt(0.2, QColor(255, 0, 0, 255))
        animation.setEndValue(QColor(255, 0, 0, 0))
        animation.valueChanged.connect(band.setColor)
        animation.finished.connect(lambda: self.remove_flash(animation))
        self.flashes[animation] = band
        animation.start()

    def remove_flash(self, animation, notify=True):
        band = self.flashes.pop(animation, None)
        if band is None:
            return
        animation.stop()
        animation.valueChanged.disconnect()
        animation.finished.disconnect()
        self.canvas.scene().removeItem(band)
        animation.deleteLater()
        if notify:
            self.changed()

    def clear(self):
        for animation in list(self.flashes):
            self.remove_flash(animation, notify=False)
        for item in (self.marker, self.band):
            if item is not None:
                self.canvas.scene().removeItem(item)
        self.marker = None
        self.band = None
        self.changed()


def download_geometry(result, bridge=None):
    """Add one cached result to a plugin-owned memory layer, without duplicate IDs."""
    if not result.geometry:
        raise ValueError("Об’єкт не має геометрії для завантаження.")
    from qgis.core import QgsJsonUtils

    features = QgsJsonUtils.stringToFeatureList(
        json.dumps({"type": "Feature", "properties": {}, "geometry": result.geometry})
    )
    if not features or features[0].geometry().isNull():
        raise ValueError("Некоректна геометрія об’єкта.")
    geom = QgsGeometry(features[0].geometry())
    family = QgsWkbTypes.geometryDisplayString(geom.type())
    type_name = {"Point": "MultiPoint", "Line": "MultiLineString", "Polygon": "MultiPolygon"}.get(
        family
    )
    if type_name is None:
        raise ValueError("Цей тип геометрії не підтримується.")
    project = QgsProject.instance()
    layer = next(
        (
            item
            for item in project.mapLayers().values()
            if isinstance(item, QgsVectorLayer)
            and item.customProperty("ua_address_search/family", "") == family
            and item.providerType() == "memory"
            and item.crs().authid() == "EPSG:4326"
            and all(
                item.fields().indexFromName(key) >= 0
                for key in ("visicom_id", "name", "address", "category", "attribution")
            )
        ),
        None,
    )
    new_layer = layer is None
    if new_layer:
        names = {"Point": "точки", "Line": "лінії", "Polygon": "полігони"}
        layer = QgsVectorLayer(
            type_name + "?crs=EPSG:4326", "UA Address Search — " + names[family], "memory"
        )
        layer.setCustomProperty("ua_address_search/family", family)
        layer.dataProvider().addAttributes(
            [
                QgsField(key, QVariant.String)
                for key in ("visicom_id", "name", "address", "category", "attribution")
            ]
        )
        layer.updateFields()
    index = layer.fields().indexFromName("visicom_id")
    if any(feature[index] == result.id for feature in layer.getFeatures()):
        return layer, False
    if not geom.isMultipart():
        geom.convertToMultiType()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geom)
    feature.setAttributes(
        [result.id, result.label, result.address, result.category, result.attribution]
    )
    success, _ = layer.dataProvider().addFeatures([feature])
    if not success:
        raise ValueError("Не вдалося додати об’єкт до тимчасового шару.")
    layer.updateExtents()
    if new_layer:
        if bridge is None:
            project.addMapLayer(layer)
        else:
            auto_setup = bridge.autoSetupOnFirstLayer()
            try:
                bridge.setAutoSetupOnFirstLayer(False)
                project.addMapLayer(layer)
                # Consume the deferred first-layer update before restoring its policy.
                bridge.setCanvasLayers()
            finally:
                bridge.setAutoSetupOnFirstLayer(auto_setup)
    layer.triggerRepaint()
    return layer, True
