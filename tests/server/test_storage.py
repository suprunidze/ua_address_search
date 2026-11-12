from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from server.app.storage import AccessError, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite")
        self.store = Store(self.path, daily=5, per_token=3, per_minute=50)
        self.token = self.store.issue("test")
        self.digest = self.store.authenticate(self.token)

    def tearDown(self):
        self.temp.cleanup()

    def test_revocation_and_hash_only(self):
        self.assertNotIn(self.token.encode(), Path(self.path).read_bytes())
        self.store.revoke("test")
        with self.assertRaises(AccessError):
            self.store.authenticate(self.token)
        with self.assertRaises(AccessError):
            self.store.reserve(self.digest)

    def test_atomic_quota_under_concurrency(self):
        def reserve(_):
            try:
                self.store.reserve(self.digest)
                return True
            except AccessError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(results), 3)
        self.assertEqual(self.store.quota(self.digest)["remaining"], 0)
        self.assertEqual(Store(self.path, 5, 3, 50).quota(self.digest)["remaining"], 0)

    def test_global_quota_across_tokens(self):
        other = self.store.authenticate(self.store.issue("other"))
        for _ in range(3):
            self.store.reserve(self.digest)
        for _ in range(2):
            self.store.reserve(other)
        with self.assertRaises(AccessError):
            self.store.reserve(other)

    def test_kyiv_midnight_and_minute_limit(self):
        before = datetime(2026, 11, 12, 21, 59, tzinfo=timezone.utc)
        after = datetime(2026, 11, 12, 22, 0, tzinfo=timezone.utc)
        self.store.reserve(self.digest, now=before)
        self.assertEqual(self.store.quota(self.digest, after)["remaining"], 3)
        limited = Store(self.path, 5, 3, 1)
        with self.assertRaises(AccessError) as caught:
            limited.reserve(self.digest, now=before)
        self.assertEqual(caught.exception.code, "rate_limit")
        limited.reserve(self.digest, now=after)

    def test_quota_poll_does_not_use_upstream_budget(self):
        self.store.reserve(self.digest, upstream=False)
        self.assertEqual(self.store.quota(self.digest)["remaining"], 3)
