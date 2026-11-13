"""Eight Processing algorithms; only the user's own Visicom key is accepted."""

import json
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import (
    NULL,
    QgsCsException,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureSink,
    QgsGeometry,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterNumber,
    QgsProcessingParameterPoint,
    QgsProcessingParameterString,
    QgsProcessingProvider,
    QgsWkbTypes,
)
from .core.cache import SessionCache
from .core.client import ClientError, NetworkClient
from .core.operations import validate_request
from .core.qgis_io import make_feature, output_fields, result_records, source_points
from .core.settings import Settings

ALGORITHMS = (
    ("geocode", "Геокодування таблиці адрес"),
    ("reverse", "Зворотне геокодування точок"),
    ("objects", "Пошук об’єктів"),
    ("features", "Отримання об’єктів за ID"),
    ("route", "Маршрут з упорядкованих точок"),
    ("matrix", "Матриця відстаней між шарами"),
    ("optimize", "Оптимізація порядку відвідування"),
    ("snap", "Прив’язка точок до доріг"),
)


class VisicomAlgorithm(QgsProcessingAlgorithm):
    def __init__(self, operation, title, usage_changed=None):
        super().__init__()
        self.operation = operation
        self.title = title
        self.credentials = None
        self.usage_changed = usage_changed

    def name(self):
        return self.operation

    def displayName(self):
        return self.title

    def group(self):
        return "Visicom Data API"

    def groupId(self):
        return "visicom"

    def createInstance(self):
        return VisicomAlgorithm(self.operation, self.title, self.usage_changed)

    def shortHelpString(self):
        return (
            "Працює лише з власним ключем Visicom з налаштувань плагіна. "
            "Ключ зберігається у QGIS Authentication Manager. "
            "Маршрут: до 27 точок (25 проміжних); матриця: до 25 × 25; "
            "оптимізація: до 50; прив’язка та отримання за ID: до 250. "
            "Перевищення відхиляється без запиту, автоматичного розбиття немає. "
            "Для послідовності потрібне унікальне непорожнє поле порядку. "
            "Координати перетворюються в EPSG:4326; відстані повертаються в метрах. "
            "Виходи розділено за типом геометрії. STATUS позначає частковий результат. "
            "Дані: Visicom."
        )

    def initAlgorithm(self, config=None):
        op = self.operation
        if op in ("geocode", "features", "reverse", "route", "optimize", "snap"):
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    "INPUT",
                    "Вхідний шар / таблиця",
                    types=[] if op in ("geocode", "features") else [QgsProcessing.TypeVectorPoint],
                )
            )
        if op in ("geocode", "features"):
            self.addParameter(
                QgsProcessingParameterField(
                    "FIELD",
                    "Поле адреси" if op == "geocode" else "Поле ID Visicom",
                    parentLayerParameterName="INPUT",
                )
            )
        if op in ("route", "optimize", "snap"):
            self.addParameter(
                QgsProcessingParameterField(
                    "ORDER",
                    "Унікальне поле порядку",
                    parentLayerParameterName="INPUT",
                    optional=op == "snap",
                )
            )
        if op == "matrix":
            for name, title in (("ORIGINS", "Початкові точки"), ("DESTINATIONS", "Кінцеві точки")):
                self.addParameter(
                    QgsProcessingParameterFeatureSource(
                        name, title, types=[QgsProcessing.TypeVectorPoint]
                    )
                )
        if op in ("route", "matrix", "optimize", "snap"):
            self.addParameter(
                QgsProcessingParameterEnum(
                    "MODE",
                    "Режим",
                    options=(
                        ["Автомобільні дороги", "Усі дороги"]
                        if op == "snap"
                        else ["Автомобільний оптимальний", "Автомобільний найкоротший", "По прямій"]
                    ),
                    defaultValue=0,
                )
            )
        if op in ("route", "matrix", "optimize"):
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    "LOCKS",
                    "Блокування проїздів",
                    types=[QgsProcessing.TypeVectorPoint],
                    optional=True,
                )
            )
        if op == "optimize":
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "ROUND_TRIP", "Повернутися до старту", defaultValue=True
                )
            )
        if op == "snap":
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "SEPARATE", "Незалежні точки (не трек)", defaultValue=False
                )
            )
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "INTERPOLATE", "Повернути дорожню лінію", defaultValue=False
                )
            )
        if op == "objects":
            self.addParameter(
                QgsProcessingParameterString(
                    "CATEGORIES", "Коди категорій через кому", defaultValue="adm_settlement"
                )
            )
            self.addParameter(QgsProcessingParameterString("TEXT", "Текст", optional=True))
            self.addParameter(
                QgsProcessingParameterNumber(
                    "LIMIT",
                    "Ліміт результатів",
                    type=QgsProcessingParameterNumber.Integer,
                    defaultValue=100,
                    minValue=1,
                    maxValue=250,
                )
            )
            self.addParameter(
                QgsProcessingParameterEnum(
                    "SCOPE",
                    "Область",
                    options=["Україна", "Радіус", "Охоплення", "Полігон"],
                    defaultValue=0,
                )
            )
            self.addParameter(QgsProcessingParameterPoint("CENTER", "Центр радіуса", optional=True))
            self.addParameter(
                QgsProcessingParameterNumber(
                    "RADIUS", "Радіус, м", defaultValue=1000, minValue=1, maxValue=20_000_000
                )
            )
            self.addParameter(QgsProcessingParameterExtent("EXTENT", "Охоплення", optional=True))
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    "AREA", "Один полігон", types=[QgsProcessing.TypeVectorPolygon], optional=True
                )
            )
        for name, title in (
            ("POINTS", "Точки"),
            ("LINES", "Лінії"),
            ("POLYGONS", "Полігони"),
            ("TABLE", "Таблиця / без геометрії"),
        ):
            self.addParameter(QgsProcessingParameterFeatureSink(name, title))
        self.addOutput(QgsProcessingOutputString("STATUS", "Стан виконання"))

    def prepareAlgorithm(self, parameters, context, feedback):
        try:
            self.credentials = Settings().credentials(own_key=True)
            if self.credentials.mode != "visicom" or not self.credentials.token:
                raise ValueError(
                    "Processing потребує власного ключа Visicom API. "
                    "Збережіть його в налаштуваннях плагіна."
                )
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from None
        return True

    def _source(self, name, parameters, context):
        source = self.parameterAsSource(parameters, name, context)
        if source is None:
            raise ValueError("Не задано вхідний шар: " + name)
        return source

    def _jobs(self, parameters, context):
        """Preflight every input before the first chargeable request."""
        op = self.operation
        ids = {}
        if op in ("geocode", "features"):
            source = self._source("INPUT", parameters, context)
            field = self.parameterAsString(parameters, "FIELD", context)
            if source.fields().indexFromName(field) < 0:
                raise ValueError("Виберіть поле адреси / ID.")
            features = sorted(source.getFeatures(), key=lambda f: f.id())
            if not features:
                raise ValueError("Вхідна таблиця порожня.")
            if any(f[field] is None or f[field] == NULL for f in features):
                raise ValueError("Поле адреси / ID містить порожні значення.")
            if op == "features":
                value = validate_request(
                    "features", {"ids": [str(f[field]).strip() for f in features]}
                )
                mapping = {}
                for feature in features:
                    mapping.setdefault(str(feature[field]).strip(), []).append(str(feature.id()))
                return [(op, value, {"feature_sources": mapping})]
            jobs = []
            for feature in features:
                text = str(feature[field]).strip()
                if not 3 <= len(text) <= 500:
                    raise ValueError("Кожна адреса має містити 3–500 символів.")
                jobs.append(("search", text, {"source_id": str(feature.id())}))
            return jobs
        if op == "reverse":
            records = source_points(
                self._source("INPUT", parameters, context), context=context.transformContext()
            )
            return [("reverse", r["point"], {"source_id": r["source_id"]}) for r in records]
        if op == "objects":
            data = {
                "categories": [
                    c.strip()
                    for c in self.parameterAsString(parameters, "CATEGORIES", context).split(",")
                    if c.strip()
                ],
                "text": self.parameterAsString(parameters, "TEXT", context),
                "limit": self.parameterAsInt(parameters, "LIMIT", context),
            }
            scope = self.parameterAsEnum(parameters, "SCOPE", context)
            if scope == 1:
                if not parameters.get("CENTER"):
                    raise ValueError("Для радіуса задайте центральну точку з CRS.")
                center_crs = self.parameterAsPointCrs(parameters, "CENTER", context)
                if not center_crs.isValid():
                    raise ValueError("Центральна точка має мати відомий CRS.")
                p = self.parameterAsPoint(
                    parameters, "CENTER", context, QgsCoordinateReferenceSystem("EPSG:4326")
                )
                data.update(
                    near=[p.x(), p.y()],
                    radius=self.parameterAsDouble(parameters, "RADIUS", context),
                )
            elif scope in (2, 3):
                if scope == 2:
                    crs = self.parameterAsExtentCrs(parameters, "EXTENT", context)
                    rect = self.parameterAsExtent(parameters, "EXTENT", context)
                    geom = QgsGeometry.fromRect(rect)
                else:
                    source = self._source("AREA", parameters, context)
                    features = list(source.getFeatures())
                    if len(features) != 1:
                        raise ValueError("Виберіть рівно один полігон.")
                    crs = source.sourceCrs()
                    geom = QgsGeometry(features[0].geometry())
                if not crs.isValid() or geom.isNull() or not geom.isGeosValid():
                    raise ValueError("Область має мати відомий CRS і коректну геометрію.")
                geom.transform(
                    QgsCoordinateTransform(
                        crs, QgsCoordinateReferenceSystem("EPSG:4326"), context.transformContext()
                    )
                )
                data["area"] = json.loads(geom.asJson())
            return [(op, validate_request(op, data), {})]
        mode_index = self.parameterAsEnum(parameters, "MODE", context)
        modes = (
            ("driving", "walking") if op == "snap" else ("driving", "driving-shortest", "direct")
        )
        data = {"mode": modes[mode_index]}
        if op == "matrix":
            for name in ("origins", "destinations"):
                records = source_points(
                    self._source(name.upper(), parameters, context),
                    context=context.transformContext(),
                )
                data[name] = [r["point"] for r in records]
                ids[name] = [r["source_id"] for r in records]
        else:
            order = self.parameterAsString(parameters, "ORDER", context)
            separate = op == "snap" and self.parameterAsBool(parameters, "SEPARATE", context)
            if not order and not separate:
                raise ValueError("Для маршруту, оптимізації або треку виберіть поле порядку.")
            records = source_points(
                self._source("INPUT", parameters, context),
                order,
                context=context.transformContext(),
            )
            data["points"] = [r["point"] for r in records]
            ids["points"] = [r["source_id"] for r in records]
        if op in ("route", "matrix", "optimize"):
            locks = self.parameterAsSource(parameters, "LOCKS", context)
            data["locks"] = (
                [r["point"] for r in source_points(locks, context=context.transformContext())]
                if locks
                else []
            )
        if op == "optimize":
            data["round_trip"] = self.parameterAsBool(parameters, "ROUND_TRIP", context)
        if op == "snap":
            data.update(
                separate=self.parameterAsBool(parameters, "SEPARATE", context),
                interpolate=self.parameterAsBool(parameters, "INTERPOLATE", context),
            )
        return [(op, validate_request(op, data), ids)]

    def processAlgorithm(self, parameters, context, feedback):
        # Also guard programmatic use that bypasses prepareAlgorithm.
        if not self.credentials or self.credentials.mode != "visicom" or not self.credentials.token:
            raise QgsProcessingException("Processing потребує власного ключа Visicom API.")
        try:
            jobs = self._jobs(parameters, context)
        except (ValueError, RuntimeError, QgsCsException) as exc:
            raise QgsProcessingException(str(exc)) from None
        fields = output_fields()
        sinks = {}
        outputs = {}
        for name, kind in (
            ("POINTS", QgsWkbTypes.MultiPoint),
            ("LINES", QgsWkbTypes.MultiLineString),
            ("POLYGONS", QgsWkbTypes.MultiPolygon),
            ("TABLE", QgsWkbTypes.NoGeometry),
        ):
            sink, destination = self.parameterAsSink(
                parameters, name, context, fields, kind, QgsCoordinateReferenceSystem("EPSG:4326")
            )
            if sink is None:
                raise QgsProcessingException("Не вдалося створити вихід: " + name)
            sinks[name], outputs[name] = sink, destination
        cache = SessionCache()
        cache.set_identity(self.credentials.identity)
        if self.usage_changed is not None:
            cache.on_quota = self.usage_changed
        client = NetworkClient(cache)
        completed = 0
        status = "completed"
        feedback.pushInfo(f"Заплановано запитів: {len(jobs)}. Дані: Visicom.")
        try:
            for operation, value, ids in jobs:
                if feedback.isCanceled():
                    status = "partial_canceled"
                    break
                try:
                    result = client.request(self.credentials, operation, value, feedback)
                except ClientError as exc:
                    status = "partial_canceled" if feedback.isCanceled() else "partial_error"
                    feedback.reportError(str(exc) + " Часткові результати збережено.")
                    break
                if operation in ("search", "reverse"):
                    records = list(result_records("objects", [r.to_dict() for r in result], {}))
                    if not records:
                        records = [
                            {
                                "geometry": None,
                                "attributes": {"status": "not_found", "attribution": "Visicom"},
                            }
                        ]
                    for record in records:
                        record["attributes"]["source_id"] = ids["source_id"]
                else:
                    records = list(result_records(operation, result, value, ids))
                    if operation == "features":
                        expanded = []
                        found = {r["attributes"]["id"] for r in records}
                        for record in records:
                            for source_id in ids["feature_sources"].get(
                                record["attributes"]["id"], []
                            ):
                                expanded.append(
                                    {
                                        "geometry": record["geometry"],
                                        "attributes": dict(
                                            record["attributes"], source_id=source_id
                                        ),
                                    }
                                )
                        for identifier, source_ids in ids["feature_sources"].items():
                            if identifier not in found:
                                for source_id in source_ids:
                                    expanded.append(
                                        {
                                            "geometry": None,
                                            "attributes": {
                                                "id": identifier,
                                                "source_id": source_id,
                                                "status": "not_found",
                                                "attribution": "Visicom",
                                            },
                                        }
                                    )
                        records = expanded
                    if operation == "objects" and len(result) >= value["limit"]:
                        feedback.pushWarning("Досягнуто ліміту; вибірка може бути неповною.")
                for record in records:
                    geom = record.get("geometry")
                    name = (
                        "TABLE"
                        if not geom
                        else {
                            "Point": "POINTS",
                            "MultiPoint": "POINTS",
                            "LineString": "LINES",
                            "MultiLineString": "LINES",
                            "Polygon": "POLYGONS",
                            "MultiPolygon": "POLYGONS",
                        }[geom["type"]]
                    )
                    if not sinks[name].addFeature(
                        make_feature(record, fields), QgsFeatureSink.FastInsert
                    ):
                        raise QgsProcessingException("Не вдалося записати вихідний об’єкт.")
                completed += 1
                feedback.setProgress(100 * completed / len(jobs))
        finally:
            cache.close()
        outputs["STATUS"] = status
        feedback.pushInfo(f"Виконано {completed}/{len(jobs)}; стан: {status}.")
        return outputs


class VisicomProvider(QgsProcessingProvider):
    usage_changed = pyqtSignal()

    def id(self):
        return "visicom_data_api"

    def name(self):
        return "Visicom Data API for QGIS"

    def loadAlgorithms(self):
        for operation, title in ALGORITHMS:
            self.addAlgorithm(VisicomAlgorithm(operation, title, self.usage_changed.emit))


def register_provider():
    registry = QgsApplication.processingRegistry()
    if registry.providerById("visicom_data_api"):
        raise ValueError("Провайдер Visicom уже зареєстрований.")
    provider = VisicomProvider()
    if not registry.addProvider(provider):
        raise ValueError("Не вдалося зареєструвати Processing Visicom.")
    return provider
