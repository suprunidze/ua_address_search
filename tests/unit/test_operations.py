"""Documented API boundaries and non-address response contracts."""

import copy
import unittest
from visicom_data_api.core.operations import (
    normalize_response,
    upstream_request,
    validate_request,
    validate_result,
)
from visicom_data_api.core.cache import SessionCache

P = [30.5, 50.4]


class OperationTests(unittest.TestCase):
    def test_limits_exact_and_one_over(self):
        for op, key, size in (
            ("route", "points", 27),
            ("optimize", "points", 50),
            ("snap", "points", 250),
            ("matrix", "origins", 25),
            ("matrix", "destinations", 25),
        ):
            data = {key: [P] * size}
            if op == "matrix":
                data.setdefault("origins", [P])
                data.setdefault("destinations", [P])
            self.assertEqual(len(validate_request(op, data)[key]), size)
            data[key].append(P)
            with self.assertRaisesRegex(ValueError, str(size + 1)):
                validate_request(op, data)
        self.assertEqual(
            len(validate_request("features", {"ids": [f"ID_{i}" for i in range(250)]})["ids"]), 250
        )
        with self.assertRaises(ValueError):
            validate_request("features", {"ids": [f"ID_{i}" for i in range(251)]})
        self.assertEqual(
            validate_request("objects", {"categories": ["poi_restaurant"], "limit": 250})["limit"],
            250,
        )
        for bad in (251, 0, True, float("nan")):
            with self.assertRaises(ValueError):
                validate_request("objects", {"categories": ["adr_address"], "limit": bad})

    def test_reject_bad_inputs_and_conflicting_filters(self):
        for data in (
            {"points": [P, [181, 0]]},
            {"points": [P, [True, 0]]},
            {"points": [P]},
            {"points": [P, P], "key": "secret"},
            {"points": [P, P], "mode": "walking"},
        ):
            with self.assertRaises(ValueError):
                validate_request("route", data)
        with self.assertRaises(ValueError):
            validate_request("objects", {"categories": ["adr_address"], "radius": 10})
        with self.assertRaises(ValueError):
            validate_request("features", {"ids": ["../bad"]})
        data = validate_request("snap", {"points": [P], "separate": True, "interpolate": True})
        self.assertFalse(data["interpolate"])

    def test_routes_parameters_and_no_route(self):
        data = {"points": [P, [30.6, 50.5], P]}
        branch, path, params = upstream_request("route", data)
        self.assertEqual((branch, path), ("core", "distance.json"))
        self.assertEqual(params["geometry"], "path")
        self.assertEqual(params["waypoints"], "30.6,50.5")
        result = normalize_response("route", {}, data)
        self.assertIsNone(result["distance_m"])
        raw = {
            "geometry": {"type": "LineString", "coordinates": [P, P]},
            "properties": {"distance": 12.5, "secret": "discard"},
        }
        result = normalize_response("route", raw, data)
        self.assertEqual(result["distance_m"], 12.5)
        self.assertEqual(validate_result("route", result, data), result)
        self.assertNotIn("secret", str(result))

    def test_matrix_status_shape_and_null_distance(self):
        data = {"origins": [P], "destinations": [P, P]}
        raw = {"rows": [[{"status": "OK", "distance": 0}, {"status": "NOT_FOUND"}]]}
        result = normalize_response("matrix", raw, data)
        self.assertEqual(result["rows"][0][0]["distance_m"], 0)
        self.assertIsNone(result["rows"][0][1]["distance_m"])
        self.assertEqual(validate_result("matrix", result, data), result)
        for bad in (
            {"rows": []},
            {"rows": [[{}]]},
            {"rows": [[{"status": "OK"}, {"status": "OK", "distance": 1}]]},
        ):
            with self.assertRaises(ValueError):
                normalize_response("matrix", bad, data)

    def test_optimization_permutation_and_fixed_endpoints(self):
        data = {"points": [P] * 4, "round_trip": False}
        result = normalize_response(
            "optimize", {"list": [{"index": i} for i in [0, 2, 1, 3]]}, data
        )
        self.assertEqual(result["order"], [0, 2, 1, 3])
        for order in ([0, 1, 1, 3], [1, 0, 2, 3], [0, 1, 3, 2], [0, 1], [0, True, 2, 3]):
            with self.assertRaises(ValueError):
                normalize_response("optimize", {"list": [{"index": i} for i in order]}, data)

    def test_snap_point_collection_and_interpolated_line(self):
        feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": P}}
        result = normalize_response("snap", feature, {"points": [P]})
        self.assertEqual(result["features"][0]["source_index"], 0)
        collection = {"type": "FeatureCollection", "features": [feature, feature]}
        result = normalize_response("snap", collection, {"points": [P, P]})
        self.assertEqual(len(result["features"]), 2)
        line = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [P, P]}}
        result = normalize_response("snap", line, {"points": [P, P], "interpolate": True})
        self.assertIsNone(result["features"][0]["source_index"])
        self.assertEqual(
            validate_result("snap", result, {"points": [P, P], "interpolate": True}), result
        )
        with self.assertRaises(ValueError):
            normalize_response("snap", feature, {"points": [P, P]})

    def test_spatial_area_and_250_vertex_boundary(self):
        ring = [P] * 250
        data = {"categories": ["adr_address"], "area": {"type": "Polygon", "coordinates": [ring]}}
        self.assertIn("contains", upstream_request("objects", data)[2])
        too_many = copy.deepcopy(data)
        too_many["area"]["coordinates"][0].append(P)
        with self.assertRaises(ValueError):
            validate_request("objects", too_many)
        data["near"] = P
        data["radius"] = 100
        with self.assertRaises(ValueError):
            validate_request("objects", data)

    def test_cache_all_parameters_and_point_order(self):
        key = SessionCache.key(
            ("own", "hash"), "route", {"points": [P, [31, 51]], "mode": "direct"}
        )
        self.assertEqual(
            key,
            SessionCache.key(("own", "hash"), "route", {"mode": "direct", "points": [P, [31, 51]]}),
        )
        self.assertNotEqual(
            key,
            SessionCache.key(("own", "hash"), "route", {"mode": "direct", "points": [[31, 51], P]}),
        )
