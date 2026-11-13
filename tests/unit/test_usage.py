from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from visicom_data_api.core.usage import LocalUsage
from visicom_data_api.core.cache import SessionCache, namespace


class UsageTests(unittest.TestCase):
    def test_atomic_restart_day_and_key_isolation_without_enforcement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            usage = LocalUsage(str(path))
            with ThreadPoolExecutor(max_workers=6) as pool:
                rows = list(pool.map(lambda _: usage.record("secret", "2026-11-12", 2), range(30)))
            self.assertEqual(sorted(r["used"] for r in rows), list(range(1, 31)))
            snapshot = LocalUsage(str(path)).record("secret", "2026-11-12", 2, False)
            self.assertEqual((snapshot["remaining"], snapshot["exceeded"]), (0, 28))
            self.assertEqual(usage.record("other", "2026-11-12", 1500)["used"], 1)
            self.assertEqual(usage.record("secret", "2026-11-13", 1500)["used"], 1)
            self.assertNotIn(b"secret", path.read_bytes())

    def test_quota_snapshots_ignore_old_responses_and_old_identity(self):
        cache = SessionCache()
        identity = namespace("https://server.invalid", "private")
        cache.set_identity(identity)
        cache.update_quota(identity, {"observed_at": 20, "remaining": 5})
        cache.update_quota(identity, {"observed_at": 10, "remaining": 8})
        self.assertEqual(cache.quota["remaining"], 5)
        cache.set_identity(namespace("https://server.invalid", "new"))
        cache.update_quota(identity, {"observed_at": 30, "remaining": 0})
        self.assertIsNone(cache.quota)
        cache.close()
