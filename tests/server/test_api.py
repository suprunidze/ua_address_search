import json
from pathlib import Path
import tempfile
import unittest
import httpx
from server.app.config import Config
from server.app.main import create_app
from server.app.visicom import ProviderError, Visicom

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/address.json"


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def search(self, text):
        self.calls.append(text)
        if self.failure:
            raise self.failure
        return []

    async def reverse(self, x, y):
        self.calls.append((x, y))
        return []

    async def feature(self, identifier):
        self.calls.append(identifier)
        return []


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        config = Config("fake-key", str(Path(self.temp.name) / "test.sqlite"), 20, 10, 20)
        self.app = create_app(config, self.provider)
        self.token = self.app.state.store.issue("test")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.headers = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_health_and_authentication(self):
        self.assertEqual((await self.client.get("/healthz")).status_code, 200)
        response = await self.client.post("/v1/search", json={"text": "Київ"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.provider.calls, [])
        response = await self.client.post("/v1/search", json={"text": "Київ"}, headers=self.headers)
        self.assertEqual(response.json()["results"], [])
        self.assertEqual(response.json()["quota"]["remaining"], 9)
        self.app.state.store.revoke("test")
        self.assertEqual(
            (await self.client.get("/v1/quota", headers=self.headers)).status_code, 401
        )

    async def test_validation_and_routes(self):
        for body in ({"text": " "}, {"text": "Київ", "key": "not-allowed"}):
            response = await self.client.post("/v1/search", json=body, headers=self.headers)
            self.assertEqual(response.status_code, 422)
        response = await self.client.post(
            "/v1/reverse", json={"longitude": 181, "latitude": 0}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)
        response = await self.client.post(
            "/v1/reverse", json={"longitude": 30, "latitude": 50}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.provider.calls[-1], (30, 50))
        self.assertEqual(
            (await self.client.get("/v1/features/ADR_TEST", headers=self.headers)).status_code, 200
        )
        self.assertEqual(
            (await self.client.get("/v1/features/bad.id", headers=self.headers)).status_code, 422
        )
        response = await self.client.get("/v1/quota", headers=self.headers)
        self.assertEqual(response.json()["remaining"], 8)

    async def test_timeout_does_not_refund_or_expose_key(self):
        self.provider.failure = ProviderError("provider_timeout", 504)
        response = await self.client.post("/v1/search", json={"text": "Київ"}, headers=self.headers)
        self.assertEqual(response.status_code, 504)
        self.assertNotIn("fake-key", response.text)
        quota = await self.client.get("/v1/quota", headers=self.headers)
        self.assertEqual(quota.json()["remaining"], 9)

    async def test_daily_quota_and_request_size(self):
        for _ in range(10):
            await self.client.post("/v1/search", json={"text": "Київ"}, headers=self.headers)
        response = await self.client.post("/v1/search", json={"text": "Київ"}, headers=self.headers)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "daily_quota")
        response = await self.client.post("/v1/search", content=b"x" * 65537, headers=self.headers)
        self.assertEqual(response.status_code, 413)

    async def test_chunked_request_size(self):
        async def stream():
            yield b"x" * 40000
            yield b"y" * 40000

        response = await self.client.post("/v1/search", content=stream(), headers=self.headers)
        self.assertEqual(response.status_code, 413)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_contract_and_both_shapes(self):
        feature = json.loads(FIXTURE.read_text(encoding="utf-8"))
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json=feature)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = Visicom("synthetic-key", client)
            self.assertEqual(len(await provider.search("Київ")), 1)
            self.assertEqual(calls[0].url.params["limit"], "10")
            self.assertEqual(calls[0].url.params["country"], "ua")
            await provider.reverse(30, 50)
            self.assertEqual(calls[-1].url.params["near"], "30,50")
            self.assertEqual(calls[-1].url.params["radius"], "50")
            await provider.feature("ADR_TEST")
            self.assertTrue(calls[-1].url.path.endswith("/feature/ADR_TEST.json"))

    async def test_errors_and_no_retry(self):
        for status, payload, code in (
            (429, {}, "provider_quota"),
            (403, {}, "provider_auth"),
            (500, {}, "provider_unavailable"),
            (302, {}, "provider_unavailable"),
            (200, {}, "provider_invalid_response"),
            (200, None, "provider_invalid_response"),
        ):
            count = 0

            async def handler(request):
                nonlocal count
                count += 1
                return httpx.Response(
                    status, content=b"not json" if payload is None else json.dumps(payload).encode()
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ProviderError) as caught:
                    await Visicom("synthetic-key", client).search("Київ")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(count, 1)

    async def test_network_failure(self):
        for error in (httpx.ReadTimeout("secret-url"), httpx.ConnectError("secret-url")):

            async def handler(request):
                raise error

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(ProviderError) as caught:
                    await Visicom("synthetic-key", client).search("Київ")
                self.assertNotIn("secret", str(caught.exception))
