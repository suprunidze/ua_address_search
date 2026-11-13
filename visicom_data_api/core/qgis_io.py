"""CRS-safe inputs and consistent QGIS outputs for dock and Processing."""

import json
import math
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    NULL,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsJsonUtils,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)
from .models import point


def source_points(source, order_field="", selected=False, context=None):
    """Read points in explicit unique order, or stable feature-ID order."""
    if not source or not source.sourceCrs().isValid():
        raise ValueError("Вхідний шар має мати відому систему координат.")
    transform = QgsCoordinateTransform(
        source.sourceCrs(),
        QgsCoordinateReferenceSystem("EPSG:4326"),
        context or QgsProject.instance().transformContext(),
    )
    features = list(source.getSelectedFeatures() if selected else source.getFeatures())
    if not features:
        raise ValueError("Вхідний набір точок порожній.")
    if order_field:
        if source.fields().indexFromName(order_field) < 0:
            raise ValueError("Поле порядку відсутнє.")
        keys = [f[order_field] for f in features]
        if any(
            k is None
            or k == NULL
            or (hasattr(k, "isNull") and k.isNull())
            or (isinstance(k, float) and not math.isfinite(k))
            for k in keys
        ):
            raise ValueError("Поле порядку містить порожні значення.")
        try:
            if len(set(keys)) != len(keys):
                raise ValueError("Поле порядку містить дублікати.")
            features.sort(key=lambda f: f[order_field])
        except TypeError:
            raise ValueError("Поле порядку має несумісні типи значень.") from None
    else:
        features.sort(key=lambda f: f.id())
    records = []
    for feature in features:
        geom = feature.geometry()
        if (
            geom.isNull()
            or geom.isEmpty()
            or geom.isMultipart()
            or QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.PointGeometry
        ):
            raise ValueError("Потрібні непорожні одиночні точки.")
        p = transform.transform(QgsPointXY(geom.asPoint()))
        records.append({"source_id": str(feature.id()), "point": list(point([p.x(), p.y()]))})
    return records


def output_fields():
    fields = QgsFields()
    for name in (
        "source_id",
        "source_ids",
        "origin_id",
        "destination_id",
        "id",
        "label",
        "address",
        "category",
        "status",
        "attribution",
    ):
        fields.append(QgsField(name, QVariant.String))
    for name in ("rank", "visit_order", "source_index"):
        fields.append(QgsField(name, QVariant.Int))
    fields.append(QgsField("distance_m", QVariant.Double))
    return fields


def result_records(operation, result, request, ids=None):
    """Produce geometry plus safe scalar attributes, keeping input references."""
    ids = ids or {}
    if operation in ("objects", "features"):
        for rank, item in enumerate(result, 1):
            attrs = {k: item.get(k) for k in ("id", "label", "address", "category")}
            attrs.update(rank=rank, status="OK", attribution=item.get("attribution", "Visicom"))
            yield {
                "geometry": item.get("geometry")
                or {"type": "Point", "coordinates": item["center"]},
                "attributes": attrs,
            }
    elif operation == "route":
        source_ids = [str(i) for i in ids.get("points", range(len(request["points"])))]
        yield {
            "geometry": result["geometry"],
            "attributes": {
                "distance_m": result["distance_m"],
                "status": result["status"],
                "attribution": "Visicom",
                "origin_id": source_ids[0],
                "destination_id": source_ids[-1],
                "source_ids": json.dumps(source_ids, ensure_ascii=False),
            },
        }
    elif operation == "matrix":
        for i, row in enumerate(result["rows"]):
            for j, cell in enumerate(row):
                yield {
                    "geometry": None,
                    "attributes": dict(
                        cell,
                        origin_id=str(ids.get("origins", list(range(len(result["rows"]))))[i]),
                        destination_id=str(ids.get("destinations", list(range(len(row))))[j]),
                        attribution="Visicom",
                    ),
                }
    elif operation == "optimize":
        for order, index in enumerate(result["order"], 1):
            yield {
                "geometry": {"type": "Point", "coordinates": request["points"][index]},
                "attributes": {
                    "source_id": str(ids.get("points", list(range(len(request["points"]))))[index]),
                    "source_index": index,
                    "visit_order": order,
                    "status": "OK",
                    "attribution": "Visicom",
                },
            }
    elif operation == "snap":
        for item in result["features"]:
            index = item["source_index"]
            attrs = {"source_index": index, "status": item["status"], "attribution": "Visicom"}
            if index is not None:
                attrs["source_id"] = str(
                    ids.get("points", list(range(len(request["points"]))))[index]
                )
            else:
                attrs["source_ids"] = json.dumps(
                    [str(i) for i in ids.get("points", range(len(request["points"])))],
                    ensure_ascii=False,
                )
            yield {"geometry": item["geometry"], "attributes": attrs}


def make_feature(record, fields, multi=True):
    feature = QgsFeature(fields)
    attrs = record["attributes"]
    feature.setAttributes([attrs.get(field.name()) for field in fields])
    if record.get("geometry"):
        features = QgsJsonUtils.stringToFeatureList(
            json.dumps({"type": "Feature", "geometry": record["geometry"], "properties": {}})
        )
        if not features:
            raise ValueError("Результат містить некоректну геометрію.")
        geom = QgsGeometry(features[0].geometry())
        if geom.isNull() or geom.isEmpty():
            raise ValueError("Результат містить некоректну геометрію.")
        if multi:
            geom.convertToMultiType()
        feature.setGeometry(geom)
    return feature


def memory_outputs(records, title):
    """Split mixed object geometries without lossy coercion."""
    groups = {}
    for record in records:
        geom = record.get("geometry")
        kind = "None"
        if geom:
            kind = {
                "Point": "MultiPoint",
                "MultiPoint": "MultiPoint",
                "LineString": "MultiLineString",
                "MultiLineString": "MultiLineString",
                "Polygon": "MultiPolygon",
                "MultiPolygon": "MultiPolygon",
            }[geom["type"]]
        groups.setdefault(kind, []).append(record)
    layers = []
    for kind, items in groups.items():
        layer = QgsVectorLayer(kind + "?crs=EPSG:4326", title + " — " + kind, "memory")
        if not layer.isValid():
            raise ValueError("Не вдалося створити шар результатів.")
        provider = layer.dataProvider()
        fields = output_fields()
        provider.addAttributes(fields)
        layer.updateFields()
        ok, _ = provider.addFeatures([make_feature(r, fields) for r in items])
        if not ok:
            raise ValueError("Не вдалося додати результати до шару.")
        layer.updateExtents()
        layer.setAbstract("Visicom Data API; координати EPSG:4326; відстані в метрах.")
        layers.append(layer)
    return layers
