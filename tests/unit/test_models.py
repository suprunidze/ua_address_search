import copy
import json
from pathlib import Path
import unittest
from visicom_data_api.core.models import InvalidProviderData, SearchResult, normalize, point

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/address.json"


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.feature = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_single_collection_and_roundtrip(self):
        one = normalize(self.feature)[0]
        collection = normalize({"type": "FeatureCollection", "features": [self.feature]})
        self.assertEqual(one, collection[0])
        self.assertEqual(one, SearchResult.from_dict(json.loads(json.dumps(one.to_dict()))))
        self.assertEqual(one.label, "Київ, Хрещатик, 26")
        self.assertEqual(normalize({"type": "FeatureCollection", "features": []}), [])

    def test_missing_optional_fields(self):
        self.feature.pop("bbox")
        self.feature["properties"] = {"name": "26", "street": "Хрещатик", "settlement": "Київ"}
        result = normalize(self.feature)[0]
        self.assertEqual(result.address, "Київ, Хрещатик, 26")
        self.assertIsNone(result.bbox)

    def test_order_and_no_arbitrary_provider_fields(self):
        second = copy.deepcopy(self.feature)
        second["id"] = "ADR_TEST_2"
        self.feature["url"] = "https://example.invalid/?key=secret"
        records = normalize({"type": "FeatureCollection", "features": [self.feature, second]})
        self.assertEqual([r.id for r in records], ["ADR_TEST_1", "ADR_TEST_2"])
        self.assertNotIn("url", records[0].to_dict())
        self.assertNotIn("secret", json.dumps(records[0].to_dict()))

    def test_invalid_or_missing_critical_fields(self):
        for value in (None, [], {}, {"type": "FeatureCollection", "features": None}):
            with self.subTest(value=value), self.assertRaises(InvalidProviderData):
                normalize(value)
        self.feature.pop("geo_centroid")
        with self.assertRaises(InvalidProviderData):
            normalize(self.feature)

    def test_coordinate_edges(self):
        self.assertEqual(point([-180, 90]), (-180, 90))
        for value in ([181, 0], [0, 91], [True, 0], [float("nan"), 0], ["30", 50]):
            with self.subTest(value=value), self.assertRaises(InvalidProviderData):
                point(value)

    def test_geometry_and_invalid_bounds(self):
        self.feature["geometry"] = {"type": "Point", "coordinates": [30.5234, 50.4501]}
        self.assertIsNotNone(normalize(self.feature)[0].geometry)
        self.feature["bbox"] = [31, 50, 30, 51]
        with self.assertRaises(InvalidProviderData):
            normalize(self.feature)
