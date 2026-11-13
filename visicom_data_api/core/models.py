"""Provider-independent, JSON-safe address results (no QGIS dependency)."""

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, List, Optional, Tuple


class InvalidProviderData(ValueError):
    """The provider returned an unsupported or invalid record."""


def point(value: Any) -> Tuple[float, float]:
    """Validate a WGS84 longitude/latitude pair."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise InvalidProviderData("Invalid point")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise InvalidProviderData("Invalid coordinate type")
    x, y = float(value[0]), float(value[1])
    if not math.isfinite(x) or not math.isfinite(y) or not -180 <= x <= 180 or not -90 <= y <= 90:
        raise InvalidProviderData("Coordinates outside WGS84")
    return x, y


def bounds(value: Any) -> Optional[Tuple[float, float, float, float]]:
    """Validate optional west/south/east/north bounds."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise InvalidProviderData("Invalid bounds")
    west, south = point(value[:2])
    east, north = point(value[2:])
    if west > east or south > north:
        raise InvalidProviderData("Reversed bounds")
    return west, south, east, north


def geometry(value: Any) -> Optional[Dict[str, Any]]:
    """Validate geographic GeoJSON geometry without retaining arbitrary properties."""
    if value is None:
        return None
    depths = {
        "Point": 0,
        "MultiPoint": 1,
        "LineString": 1,
        "MultiLineString": 2,
        "Polygon": 2,
        "MultiPolygon": 3,
    }
    if not isinstance(value, dict) or value.get("type") not in depths:
        raise InvalidProviderData("Unsupported geometry")

    def coordinates(items: Any, depth: int) -> Any:
        if depth == 0:
            return list(point(items))
        if not isinstance(items, list) or not items:
            raise InvalidProviderData("Empty geometry")
        return [coordinates(item, depth - 1) for item in items]

    kind = value["type"]
    return {"type": kind, "coordinates": coordinates(value.get("coordinates"), depths[kind])}


@dataclass(frozen=True)
class SearchResult:
    id: str
    label: str
    address: str
    category: str
    center: Tuple[float, float]
    bbox: Optional[Tuple[float, float, float, float]] = None
    attribution: str = "Visicom"
    geometry: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "SearchResult":
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise InvalidProviderData("Missing result identifier")
        return cls(
            id=value["id"],
            label=str(value.get("label", value["id"])),
            address=str(value.get("address", "")),
            category=str(value.get("category", "")),
            center=point(value.get("center")),
            bbox=bounds(value.get("bbox")),
            attribution=str(value.get("attribution", "Visicom")),
            geometry=geometry(value.get("geometry")),
        )


def normalize(payload: Any) -> List[SearchResult]:
    """Normalize Feature and FeatureCollection, preserving provider order."""
    if not isinstance(payload, dict):
        raise InvalidProviderData("Expected GeoJSON object")
    if payload.get("type") == "FeatureCollection":
        features = payload.get("features")
    elif payload.get("type") == "Feature":
        features = [payload]
    else:
        raise InvalidProviderData("Expected Feature or FeatureCollection")
    if not isinstance(features, list):
        raise InvalidProviderData("Expected features array")
    results = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise InvalidProviderData("Invalid feature")
        props = feature.get("properties")
        identifier = feature.get("id")
        if not isinstance(props, dict) or not isinstance(identifier, str) or not identifier:
            raise InvalidProviderData("Missing feature properties or identifier")
        centroid = feature.get("geo_centroid")
        geom = feature.get("geometry")
        if centroid is None and isinstance(geom, dict) and geom.get("type") == "Point":
            centroid = geom
        if not isinstance(centroid, dict) or centroid.get("type") != "Point":
            raise InvalidProviderData("Missing geographic center")
        address = props.get("address")
        if not isinstance(address, str):
            address = ", ".join(
                str(props[k]) for k in ("settlement", "street", "name") if props.get(k)
            )
        name = str(props.get("name") or address or identifier)
        results.append(
            SearchResult(
                id=identifier,
                label=address or name,
                address=address,
                category=str(props.get("categories", "")),
                center=point(centroid.get("coordinates")),
                bbox=bounds(feature.get("bbox")),
                attribution=str(props.get("copyright") or "Visicom"),
                geometry=geometry(geom),
            )
        )
    return results
