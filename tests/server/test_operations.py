import json
from pathlib import Path
import tempfile
import unittest
import httpx
from server.app.config import Config
from server.app.main import create_app
from server.app.visicom import ProviderError, Visicom
from visicom_data_api.core.operations import PATHS, normalize_response

P = [30.5, 50.4]
FEATURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/address.json").read_text(encoding="utf-8")
)
REQUESTS = {
    "objects": {"categories": ["adr_address"]},
    "features": {"ids": ["ADR_TEST"]},
    "route": {"points": [P, P]},
    "matrix": {"origins": [P], "destinations": [P]},
    "optimize": {"points": [P, P]},
    "snap": {"points": [P]},
}
PAYLOADS = {
    "objects": FEATURE,
    "features": FEATURE,
    "route": {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [P, P]},
        "properties": {"distance": 42},
    },
    "matrix": {"rows": [[{"status": "OK", "distance": 1}]]},
    "optimize": {"list": [{"index": 0}, {"index": 1}]},
    "snap": {"type": "Feature", "geometry": {"type": "Point", "coordinates": P}},
}


class ExpandedAPI(unittest.IsolatedAsyncioTestCase):
    async def test_routes_reserve_once_and_preflight_before_provider(self):
        class Provider:
            calls = []

            async def operation(self, operation, data):
                self.calls.append(operation)
                payload = PAYLOADS[operation]
                if operation == "snap" and len(data["points"]) > 1:
                    payload = {
                        "type": "FeatureCollection",
                        "features": [PAYLOADS["snap"] for _ in data["points"]],
                    }
                return normalize_response(operation, payload, data)

        with tempfile.TemporaryDirectory() as directory:
            provider = Provider()
            app = create_app(
                Config("synthetic", str(Path(directory) / "db.sqlite"), 100, 100, 100), provider
            )
            schema = app.openapi()
            for path in PATHS.values():
                self.assertIn("requestBody", schema["paths"][path]["post"])
            token = app.state.store.issue("operations")
            headers = {"Authorization": "Bearer " + token}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for index, (operation, path) in enumerate(PATHS.items(), 1):
                    with self.subTest(operation=operation):
                        response = await client.post(
                            path, json=REQUESTS[operation], headers=headers
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertIn("result", response.json())
                        self.assertEqual(response.json()["quota"]["remaining"], 100 - index)
                self.assertEqual(provider.calls, list(PATHS))
                for op, data in (
                    ("route", {"points": [P] * 28}),
                    ("matrix", {"origins": [P] * 26, "destinations": [P]}),
                    ("optimize", {"points": [P] * 51}),
                    ("snap", {"points": [P] * 251}),
                    ("features", {"ids": ["ADR_TEST"] * 251}),
                    ("objects", {"categories": ["adr_address"], "limit": 251}),
                ):
                    response = await client.post(PATHS[op], json=data, headers=headers)
                    self.assertEqual(response.status_code, 422)
                self.assertEqual(provider.calls, list(PATHS))
                quota = (await client.get("/v1/quota", headers=headers)).json()
                self.assertEqual(quota["remaining"], 94)
                # Valid JSON larger than the old 4 KiB limit is accepted.
                response = await client.post(
                    PATHS["snap"], json={"points": [P] * 250}, headers=headers
                )
                self.assertEqual(response.status_code, 200)

    async def test_adapter_branches_normalization_no_retry(self):
        for operation, data in REQUESTS.items():
            calls = []

            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=PAYLOADS[operation])

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await Visicom("synthetic-private-key", client).operation(operation, data)
                self.assertTrue(result)
                self.assertEqual(len(calls), 1)
                branch = "uk" if operation in ("objects", "features") else "core"
                self.assertIn("/5.0/" + branch + "/", calls[0].url.path)
                self.assertNotIn("synthetic-private-key", str(result))
        calls = []

        def fail(request):
            calls.append(request)
            return httpx.Response(200, json={"list": [{"index": 99}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(ProviderError):
                await Visicom("synthetic", client).operation("optimize", REQUESTS["optimize"])
        self.assertEqual(len(calls), 1)
