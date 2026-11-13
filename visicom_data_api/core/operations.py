"""Validated, QGIS-independent Visicom operation contracts."""

from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Dict, List, Optional

from .models import InvalidProviderData, geometry, normalize, point

OPERATIONS = ("objects", "features", "route", "matrix", "optimize", "snap")
MODES = ("driving", "driving-shortest", "direct")
PATHS = {
    "objects": "/v1/objects/search",
    "features": "/v1/features",
    "route": "/v1/route",
    "matrix": "/v1/matrix",
    "optimize": "/v1/optimize",
    "snap": "/v1/snap",
}


def limited(items: Any, maximum: int, label: str, minimum: int = 1) -> list:
    """Reject oversized jobs before any network activity; never split them."""
    if not isinstance(items, (list, tuple)):
        raise ValueError(label + ": потрібен список.")
    if not minimum <= len(items) <= maximum:
        raise ValueError(
            f"{label}: отримано {len(items)}, дозволено {minimum}–{maximum}. "
            "Автоматичного розбиття немає."
        )
    return list(items)


def points(items: Any, maximum: int, label: str, minimum: int = 1) -> list:
    return [list(point(p)) for p in limited(items, maximum, label, minimum)]


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Очікується логічне значення.")
    return value


def _number(value: Any, minimum: float, maximum: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + ": потрібне число.")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label}: дозволено {minimum}–{maximum}.")
    return value


def polygon(value: Any) -> dict:
    result = geometry(value)
    if not result or result["type"] not in ("Polygon", "MultiPolygon"):
        raise ValueError("Область пошуку має бути полігоном.")
    polygons = [result["coordinates"]] if result["type"] == "Polygon" else result["coordinates"]
    total = 0
    for rings in polygons:
        for ring in rings:
            total += len(ring)
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Кільце полігона має бути замкненим і містити від 4 вершин.")
    limited([None] * total, 250, "Вершини області пошуку", 4)
    return result


def validate_request(operation: str, value: Any) -> dict:
    """Return a canonical request shared by QGIS and the proxy."""
    if operation not in OPERATIONS or not isinstance(value, dict):
        raise ValueError("Невідома операція або некоректні параметри.")
    allowed = {
        "objects": {"categories", "text", "limit", "near", "radius", "area"},
        "features": {"ids"},
        "route": {"points", "locks", "mode"},
        "matrix": {"origins", "destinations", "locks", "mode"},
        "optimize": {"points", "locks", "mode", "round_trip"},
        "snap": {"points", "mode", "interpolate", "separate"},
    }[operation]
    if set(value) - allowed:
        raise ValueError("Зайві параметри запиту.")
    result = {}
    if operation == "objects":
        cats = limited(value.get("categories", []), 100, "Категорії")
        if any(
            not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", c) for c in cats
        ):
            raise ValueError("Некоректний ідентифікатор категорії.")
        text = value.get("text", "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError("Текст пошуку: максимум 500 символів.")
        limit = value.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 250:
            raise ValueError("Ліміт результатів: дозволено 1–250.")
        result.update(categories=sorted(set(cats)), text=text.strip(), limit=limit)
        if value.get("near") is not None:
            if value.get("area") is not None:
                raise ValueError("Виберіть радіус або полігон, а не обидва.")
            result["near"] = list(point(value["near"]))
            result["radius"] = _number(value.get("radius"), 1, 20_000_000, "Радіус, м")
        elif "radius" in value:
            raise ValueError("Для радіуса потрібна центральна точка.")
        if value.get("area") is not None:
            result["area"] = polygon(value["area"])
    elif operation == "features":
        ids = limited(value.get("ids", []), 250, "Ідентифікатори")
        if any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", i) for i in ids):
            raise ValueError("Некоректний ідентифікатор об’єкта.")
        result["ids"] = list(dict.fromkeys(ids))
    else:
        if operation == "matrix":
            for name in ("origins", "destinations"):
                result[name] = points(value.get(name, []), 25, name)
        else:
            maximum, minimum = {"route": (27, 2), "optimize": (50, 2), "snap": (250, 1)}[operation]
            result["points"] = points(value.get("points", []), maximum, "Точки", minimum)
        result["mode"] = value.get("mode", "driving")
        modes = ("driving", "walking") if operation == "snap" else MODES
        if result["mode"] not in modes:
            raise ValueError("Непідтримуваний режим.")
        if operation != "snap":
            result["locks"] = points(value.get("locks", []), 250, "Блокування", 0)
            if result["locks"] and result["mode"] == "direct":
                raise ValueError("Блокування застосовуються лише до автомобільних маршрутів.")
        if operation == "optimize":
            result["round_trip"] = _boolean(value.get("round_trip", True))
        if operation == "snap":
            result["separate"] = _boolean(value.get("separate", False))
            result["interpolate"] = _boolean(value.get("interpolate", False))
            if result["separate"]:
                result["interpolate"] = False
    return result


def _coordinates(items: list) -> str:
    return "|".join(f"{x:.12g},{y:.12g}" for x, y in items)


def _wkt(value: dict) -> str:
    def ring(items):
        return "(" + ",".join(f"{x:.12g} {y:.12g}" for x, y in items) + ")"

    def poly(items):
        return "(" + ",".join(ring(r) for r in items) + ")"

    if value["type"] == "Polygon":
        return "POLYGON" + poly(value["coordinates"])
    return "MULTIPOLYGON(" + ",".join(poly(p) for p in value["coordinates"]) + ")"


def upstream_request(operation: str, value: dict):
    """Return the fixed API branch, path and parameters, without credentials."""
    data = validate_request(operation, value)
    if operation == "objects":
        params = {
            "categories": ",".join(data["categories"]),
            "country": "ua",
            "limit": data["limit"],
        }
        if data["text"]:
            params["text"] = data["text"]
        if "near" in data:
            params.update(
                near=_coordinates([data["near"]]), radius=data["radius"], order="distance"
            )
        if "area" in data:
            params["contains"] = _wkt(data["area"])
        return "uk", "geocode.json", params
    if operation == "features":
        return "uk", "feature/" + ",".join(data["ids"]) + ".json", {}
    params = {"mode": data["mode"]}
    if data.get("locks"):
        params["locks"] = _coordinates(data["locks"])
    if operation == "route":
        params.update(
            origin=_coordinates(data["points"][:1]),
            destination=_coordinates(data["points"][-1:]),
            geometry="path",
        )
        if len(data["points"]) > 2:
            params["waypoints"] = _coordinates(data["points"][1:-1])
        path = "distance.json"
    elif operation == "matrix":
        params.update(
            origins=_coordinates(data["origins"]), destinations=_coordinates(data["destinations"])
        )
        path = "distancematrix.json"
    elif operation == "optimize":
        params.update(
            waypoints=_coordinates(data["points"]), round_trip=str(data["round_trip"]).lower()
        )
        path = "tsp.json"
    else:
        params.update(
            points=_coordinates(data["points"]),
            separate=str(data["separate"]).lower(),
            interpolate=str(data["interpolate"]).lower(),
        )
        path = "snaptoroad.json"
    return "core", path, params


@dataclass(frozen=True)
class RouteResult:
    distance_m: Optional[float]
    geometry: Optional[Dict[str, Any]]
    status: str
    attribution: str = "Visicom"


@dataclass(frozen=True)
class MatrixResult:
    rows: List[List[dict]]
    attribution: str = "Visicom"


@dataclass(frozen=True)
class OptimizationResult:
    order: List[int]
    attribution: str = "Visicom"


@dataclass(frozen=True)
class SnapResult:
    features: List[dict]
    attribution: str = "Visicom"


def normalize_response(operation: str, payload: Any, request: dict) -> Any:
    """Normalize provider responses without assuming address IDs or centroids."""
    data = validate_request(operation, request)
    if not isinstance(payload, dict):
        raise InvalidProviderData("Expected object")
    if operation in ("objects", "features"):
        return [r.to_dict() for r in normalize(payload)]
    if operation == "route":
        if not payload:
            return asdict(RouteResult(None, None, "not_found"))
        geom = geometry(payload.get("geometry"))
        if not geom or geom["type"] != "LineString" or len(geom["coordinates"]) < 2:
            raise InvalidProviderData("Expected route line")
        distance = _number(payload.get("properties", {}).get("distance"), 0, 1e12, "Відстань")
        return asdict(RouteResult(distance, geom, "OK"))
    if operation == "matrix":
        rows = payload.get("rows")
        if not isinstance(rows, list) or len(rows) != len(data["origins"]):
            raise InvalidProviderData("Invalid matrix rows")
        result = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(data["destinations"]):
                raise InvalidProviderData("Invalid matrix columns")
            values = []
            for cell in row:
                if not isinstance(cell, dict) or not isinstance(cell.get("status"), str):
                    raise InvalidProviderData("Invalid matrix status")
                distance = (
                    _number(cell.get("distance"), 0, 1e12, "Відстань")
                    if cell["status"] == "OK"
                    else None
                )
                status = cell["status"]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", status):
                    raise InvalidProviderData("Invalid matrix status")
                values.append({"distance_m": distance, "status": status})
            result.append(values)
        return asdict(MatrixResult(result))
    if operation == "optimize":
        records = payload.get("list")
        if not isinstance(records, list) or any(not isinstance(p, dict) for p in records):
            raise InvalidProviderData("Invalid optimization")
        order = [p.get("index") for p in records]
        size = len(data["points"])
        if (
            any(type(i) is not int for i in order)
            or len(order) != size
            or set(order) != set(range(size))
            or order[0] != 0
            or (not data["round_trip"] and order[-1] != size - 1)
        ):
            raise InvalidProviderData("Invalid optimization order")
        return asdict(OptimizationResult(order))
    features = payload.get("features") if payload.get("type") == "FeatureCollection" else [payload]
    if not isinstance(features, list):
        raise InvalidProviderData("Invalid snap response")
    line = data["interpolate"] and len(data["points"]) > 1
    if len(features) != (1 if line else len(data["points"])):
        raise InvalidProviderData("Missing snapped points")
    result = []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise InvalidProviderData("Invalid snap feature")
        geom = geometry(feature.get("geometry"))
        if not geom or geom["type"] != ("LineString" if line else "Point"):
            raise InvalidProviderData("Invalid snapped geometry")
        if line and len(geom["coordinates"]) < 2:
            raise InvalidProviderData("Invalid snap line")
        result.append({"geometry": geom, "source_index": None if line else index, "status": "OK"})
    return asdict(SnapResult(result))


def validate_result(operation: str, payload: Any, request: dict) -> Any:
    """Validate the proxy's normalized result using the same provider contracts."""
    if operation in ("objects", "features"):
        from .models import SearchResult

        if not isinstance(payload, list):
            raise InvalidProviderData("Expected records")
        return [SearchResult.from_dict(p).to_dict() for p in payload]
    if not isinstance(payload, dict):
        raise InvalidProviderData("Expected result object")
    if operation == "route":
        raw = (
            {}
            if payload.get("status") == "not_found"
            else {
                "geometry": payload.get("geometry"),
                "properties": {"distance": payload.get("distance_m")},
            }
        )
    elif operation == "matrix":
        raw = {
            "rows": [
                [{"distance": c.get("distance_m"), "status": c.get("status")} for c in row]
                for row in payload["rows"]
            ]
        }
    elif operation == "optimize":
        raw = {"list": [{"index": i} for i in payload["order"]]}
    else:
        raw = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": f.get("geometry")} for f in payload["features"]
            ],
        }
    return normalize_response(operation, raw, request)
