import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import logging
from pathlib import Path
import secrets
import tempfile
import unittest
import uuid

import httpx
from server.app.config import Config
from server.app.main import create_app
from server.app.storage import AccessError, Store
from server.app.journal import JsonFormatter, configure


class RegistrationTests(unittest.TestCase):
    def test_atomic_idempotency_revocation_and_persistent_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "test.sqlite")
            store = Store(path)
            identifier, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
            with ThreadPoolExecutor(max_workers=5) as pool:
                rows = list(pool.map(lambda _: store.register(identifier, token), range(12)))
            self.assertEqual(sum(created for _, created in rows), 1)
            digest = store.authenticate(token)
            store.reserve(digest)
            restarted = Store(path)
            self.assertFalse(restarted.register(identifier, token, enabled=False)[1])
            self.assertEqual(restarted.quota(digest)["remaining"], 99)
            with self.assertRaises(AccessError) as error:
                restarted.register(identifier, secrets.token_urlsafe(32))
            self.assertEqual(error.exception.status, 409)
            with self.assertRaises(AccessError):
                restarted.register(str(uuid.uuid4()), secrets.token_urlsafe(32), daily_limit=1)
            store.revoke("installation-" + identifier)
            with self.assertRaises(AccessError) as error:
                restarted.register(identifier, token)
            self.assertEqual(error.exception.code, "registration_revoked")
            self.assertNotIn(token.encode(), Path(path).read_bytes())

    def test_registration_rollover_and_global_concurrent_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "test.sqlite"))
            now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)

            def enroll(_):
                try:
                    return store.register(
                        str(uuid.uuid4()),
                        secrets.token_urlsafe(32),
                        minute_limit=3,
                        daily_limit=4,
                        now=now,
                    )[1]
                except AccessError:
                    return False

            with ThreadPoolExecutor(max_workers=5) as pool:
                self.assertEqual(sum(pool.map(enroll, range(10))), 3)
            later = datetime(2026, 11, 12, 12, 2, tzinfo=timezone.utc)
            self.assertTrue(
                store.register(
                    str(uuid.uuid4()),
                    secrets.token_urlsafe(32),
                    minute_limit=3,
                    daily_limit=4,
                    now=later,
                )[1]
            )
            with self.assertRaises(AccessError):
                store.register(
                    str(uuid.uuid4()),
                    secrets.token_urlsafe(32),
                    minute_limit=3,
                    daily_limit=4,
                    now=later,
                )
            tomorrow = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
            self.assertTrue(
                store.register(
                    str(uuid.uuid4()),
                    secrets.token_urlsafe(32),
                    minute_limit=3,
                    daily_limit=4,
                    now=tomorrow,
                )[1]
            )


class RegistrationAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_contract_privacy_and_quota_on_errors(self):
        class Provider:
            async def search(self, text):
                return []

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                Config("provider-secret", str(Path(directory) / "test.sqlite")), Provider()
            )
            stream = io.StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonFormatter())
            logger = configure()
            old = logger.handlers
            logger.handlers = [handler]
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    secret, identifier = secrets.token_urlsafe(32), str(uuid.uuid4())
                    headers = {"Authorization": "Bearer " + secret}
                    replies = await asyncio.gather(
                        *[
                            client.post(
                                "/v1/register",
                                json={"installation_id": identifier},
                                headers=headers,
                            )
                            for _ in range(3)
                        ]
                    )
                    self.assertTrue(all(r.status_code == 200 for r in replies))
                    self.assertEqual(replies[0].json()["quota"]["remaining"], 100)
                    self.assertNotIn(secret, replies[0].text)
                    response = await client.post(
                        "/v1/search", json={"text": "Київ"}, headers=headers
                    )
                    self.assertEqual(response.json()["quota"]["remaining"], 99)
                    response = await client.post("/v1/search", json={"text": "x"}, headers=headers)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["quota"]["remaining"], 99)
                    app.state.store.revoke("installation-" + identifier)
                    response = await client.post(
                        "/v1/register", json={"installation_id": identifier}, headers=headers
                    )
                    self.assertEqual(response.status_code, 403)
                    response = await client.post(
                        "/v1/register", json={"installation_id": "bad"}, headers=headers
                    )
                    self.assertEqual(response.status_code, 422)
                for value in (secret, Store.digest(secret), "provider-secret", "Київ", "127.0.0.1"):
                    self.assertNotIn(value, stream.getvalue())
                self.assertIn('"client_registered"', stream.getvalue())
                self.assertIn('"registration_confirmed"', stream.getvalue())
            finally:
                logger.handlers = old
