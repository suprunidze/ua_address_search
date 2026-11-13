from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from visicom_data_api.core.cache import CacheCancelled, SessionCache, namespace


class Feedback:
    def __init__(self):
        self.event = threading.Event()

    def cancel(self):
        self.event.set()

    def isCanceled(self):
        return self.event.is_set()


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = SessionCache()
        self.identity = namespace("https://test.invalid", "synthetic-token")

    def resolve(self, value, loader, feedback=None, operation="search"):
        return self.cache.resolve(
            self.identity, operation, value, loader, feedback or Feedback(), Feedback
        )

    def test_normalized_search_empty_results_and_defensive_copy(self):
        calls = []

        def load(feedback):
            calls.append(True)
            return [{"value": [1]}]

        result, source = self.resolve(" КиЇв   26-А ", load)
        self.assertEqual(source, "network")
        result[0]["value"].append(2)
        cached, source = self.resolve("київ 26-а", load)
        self.assertEqual(source, "cache")
        self.assertEqual(cached, [{"value": [1]}])
        self.resolve("Київ 26-А/1", load)
        self.assertEqual(len(calls), 2)
        self.resolve("empty", lambda _: [])
        empty, source = self.resolve("empty", load)
        self.assertEqual((empty, source), ([], "cache"))

    def test_simultaneous_consumers_share_one_call(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def load(feedback):
            calls.append(True)
            entered.set()
            release.wait(2)
            return [1]

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(self.resolve, "Kyiv", load) for _ in range(6)]
            self.assertTrue(entered.wait(1))
            deadline = time.monotonic() + 2
            while self.cache.stats["shared"] < 5 and time.monotonic() < deadline:
                time.sleep(0.01)
            release.set()
            self.assertTrue(all(f.result()[0] == [1] for f in futures))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.cache.stats["shared"], 5)

    def test_cancel_one_preserves_other_consumer(self):
        entered, release = threading.Event(), threading.Event()
        first, second = Feedback(), Feedback()

        def load(feedback):
            entered.set()
            release.wait(2)
            self.assertFalse(feedback.isCanceled())
            return [1]

        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.resolve, "Kyiv", load, first)
            self.assertTrue(entered.wait(1))
            follower = pool.submit(self.resolve, "Kyiv", load, second)
            deadline = time.monotonic() + 1
            while self.cache.stats["shared"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            first.cancel()
            time.sleep(0.05)
            release.set()
            with self.assertRaises(CacheCancelled):
                leader.result()
            self.assertEqual(follower.result()[0], [1])
        self.assertEqual(self.resolve("Kyiv", load)[1], "cache")

    def test_all_cancel_errors_and_identity_change_do_not_cache(self):
        cancelled = Feedback()
        cancelled.cancel()
        with self.assertRaises(CacheCancelled):
            self.resolve("Kyiv", lambda _: [], cancelled)
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.resolve("bad", lambda _: (_ for _ in ()).throw(ValueError("test")))
        self.assertEqual(self.cache.stats["network"], 2)
        self.resolve("Kyiv", lambda _: [1])
        self.cache.set_identity(namespace("https://other.invalid", "synthetic-token"))
        with self.assertRaises(CacheCancelled):
            self.resolve("Kyiv", lambda _: [2])
        self.assertEqual(len(self.cache.entries), 0)

    def test_cancelled_producer_and_cleared_session_cannot_repopulate(self):
        entered = threading.Event()
        feedback = Feedback()

        def load(shared):
            entered.set()
            if not shared.event.wait(1):
                raise AssertionError("Producer was not cancelled")
            return [1]

        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.resolve, "Kyiv", load, feedback)
            self.assertTrue(entered.wait(1))
            feedback.cancel()
            with self.assertRaises(CacheCancelled):
                future.result()
        self.assertEqual(len(self.cache.entries), 0)
        self.cache.close()
        with self.assertRaises(CacheCancelled):
            self.resolve("other", lambda _: [1])

    def test_lru_and_exact_reverse_keys(self):
        self.cache = SessionCache(max_bytes=7)
        self.resolve("one", lambda _: [1])
        self.resolve("two", lambda _: [2])
        self.resolve("one", lambda _: [1])
        self.resolve("three", lambda _: [3])
        self.assertFalse(self.cache.peek(self.identity, "search", "two")[0])
        self.cache = SessionCache()
        self.resolve((30.1, 50.1), lambda _: [1], operation="reverse")
        self.assertFalse(self.cache.peek(self.identity, "reverse", (30.10000001, 50.1))[0])
