import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
import httpx
from server.app.config import Config
from server.app.journal import JsonFormatter, configure
from server.app.main import create_app
from server.app.storage import AccessError, Store


class OverrideTests(unittest.TestCase):
    def test_override_preserves_counters_and_other_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite")
            old = Store(path, 1500, 100, 100)
            token = old.issue("pilot")
            digest = old.authenticate(token)
            other = old.authenticate(old.issue("other"))
            old.reserve(digest)
            updated = Store(path, 1500, 100, 100, {digest: 1000})
            self.assertEqual(updated.quota(digest)["token_daily_limit"], 1000)
            self.assertEqual(updated.quota(digest)["remaining"], 999)
            self.assertEqual(updated.quota(other)["token_daily_limit"], 100)
            with updated.connection() as db:
                db.execute("UPDATE daily SET count=1500 WHERE scope='global'")
            self.assertEqual(updated.quota(digest)["remaining"], 0)
            with self.assertRaises(AccessError):
                updated.reserve(digest)

    def test_configuration_validation(self):
        for value in ("[]", '{"bad":1000}', '{"' + "a" * 64 + '":0}', '{"' + "a" * 64 + '":true}'):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"UAAS_TOKEN_DAILY_LIMIT_OVERRIDES": value}),
                self.assertRaises(ValueError),
            ):
                Config.from_env()
        with patch.dict(
            os.environ, {"UAAS_TOKEN_DAILY_LIMIT_OVERRIDES": json.dumps({"a" * 64: 1000})}
        ):
            self.assertEqual(Config.from_env().token_daily_overrides["a" * 64], 1000)


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_events_errors_and_sensitive_data_exclusion(self):
        class Provider:
            async def search(self, text):
                return []

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                Config("secret-provider-key", str(Path(directory) / "test.sqlite")), Provider()
            )
            token = app.state.store.issue("pilot")
            digest = app.state.store.digest(token)
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonFormatter())
            logger = configure()
            initial_handlers = list(logger.handlers)
            logger.handlers = [handler]
            try:
                configure()
                configure()
                self.assertEqual(sum(getattr(h, "uaas_stdout", False) for h in logger.handlers), 1)
                logger.handlers = [handler]
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    headers = {
                        "Authorization": "Bearer " + token,
                        "X-UAAS-Session-ID": str(uuid.uuid4()),
                    }
                    for _ in range(2):
                        response = await client.post(
                            "/v1/search", json={"text": "private-address-26"}, headers=headers
                        )
                        self.assertEqual(response.status_code, 200)
                        self.assertTrue(response.headers.get("X-Request-ID"))
                    await client.post("/v1/search", json={"text": "private-address-26"})
                    await client.post("/v1/search", json={"text": "x"}, headers=headers)
                    await client.post(
                        "/v1/search", content=b"private-address-26" * 400, headers=headers
                    )
                events = [json.loads(line) for line in stream.getvalue().splitlines()]
                self.assertEqual(sum(e["event"] == "client_connected" for e in events), 1)
                completed = [e for e in events if e["event"] == "request_completed"]
                self.assertEqual([e["status"] for e in completed], [200, 200, 401, 422, 413])
                self.assertEqual([e["upstream_calls"] for e in completed], [1, 1, 0, 0, 0])
                self.assertTrue(all("duration_ms" in e and "request_id" in e for e in completed))
                for forbidden in (
                    token,
                    digest,
                    "private-address-26",
                    "secret-provider-key",
                    "Authorization",
                ):
                    self.assertNotIn(forbidden, stream.getvalue())
            finally:
                logger.handlers = initial_handlers
