"""Thread-safe session LRU and single-flight requests shared by Locator clones."""

from collections import OrderedDict
import hashlib
import json
import threading
import unicodedata
import uuid


class CacheCancelled(Exception):
    """This consumer or its session was cancelled."""


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def namespace(url: str, token: str):
    return url.rstrip("/"), hashlib.sha256(token.encode("utf-8")).hexdigest()


class Flight:
    def __init__(self, feedback, epoch):
        self.feedback = feedback
        self.epoch = epoch
        self.consumers = []
        self.done = threading.Event()
        self.payload = None
        self.error = None


class SessionCache:
    def __init__(self, max_bytes=128 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.session_id = str(uuid.uuid4())
        self.lock = threading.RLock()
        self.entries = OrderedDict()
        self.flights = {}
        self.size = 0
        self.epoch = 0
        self.identity = None
        self.closed = False
        self.stats = {"network": 0, "cache": 0, "shared": 0}
        self.quota = None
        self.on_quota = None
        self.on_error = None

    def set_identity(self, identity):
        with self.lock:
            if self.identity == identity:
                return
            self._clear()
            self.identity = identity

    def _clear(self):
        self.epoch += 1
        self.entries.clear()
        self.size = 0
        self.quota = None
        for flight in self.flights.values():
            flight.feedback.cancel()
        self.flights.clear()

    def close(self):
        with self.lock:
            self.closed = True
            self._clear()

    def update_quota(self, identity, quota):
        with self.lock:
            if self.closed or self.identity != identity or not isinstance(quota, dict):
                return
            if self.quota is None or quota.get("observed_at", 0) >= self.quota.get(
                "observed_at", 0
            ):
                self.quota = dict(quota)
                if self.on_quota is not None:
                    self.on_quota()

    def notify_error(self, identity, code, quota, feedback):
        with self.lock:
            if not self.closed and self.identity == identity and self.on_error is not None:
                self.on_error(
                    {
                        "identity": identity,
                        "code": code,
                        "day": quota.get("day"),
                        "feedback": feedback,
                    }
                )

    @staticmethod
    def key(identity, operation, value):
        if operation == "search":
            value = normalize_text(value)
        elif operation == "reverse":
            value = tuple(value)
        return identity, operation, value

    def peek(self, identity, operation, value):
        key = self.key(identity, operation, value)
        with self.lock:
            if self.closed or identity != self.identity or key not in self.entries:
                return False, None
            payload = self.entries.pop(key)
            self.entries[key] = payload
            return True, json.loads(payload)

    def _watch(self, flight):
        while not flight.done.wait(0.02):
            with self.lock:
                active = any(not consumer.isCanceled() for consumer in flight.consumers)
            if not active:
                flight.feedback.cancel()
                return

    def resolve(self, identity, operation, value, loader, consumer, feedback_factory):
        key = self.key(identity, operation, value)
        if consumer.isCanceled():
            raise CacheCancelled()
        with self.lock:
            if self.identity is None:
                self.identity = identity
            if self.closed or self.identity != identity:
                raise CacheCancelled()
            if key in self.entries:
                payload = self.entries.pop(key)
                self.entries[key] = payload
                self.stats["cache"] += 1
                return json.loads(payload), "cache"
            flight = self.flights.get(key)
            if flight is not None and flight.feedback.isCanceled():
                flight = None
            leader = flight is None
            if leader:
                flight = Flight(feedback_factory(), self.epoch)
                self.flights[key] = flight
                self.stats["network"] += 1
            else:
                self.stats["shared"] += 1
            flight.consumers.append(consumer)
        if leader:
            monitor = threading.Thread(target=self._watch, args=(flight,), daemon=True)
            monitor.start()
            try:
                data = loader(flight.feedback)
                payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
                with self.lock:
                    if self.closed or flight.epoch != self.epoch or flight.feedback.isCanceled():
                        raise CacheCancelled()
                    flight.payload = payload
                    if len(payload) <= self.max_bytes:
                        while self.entries and self.size + len(payload) > self.max_bytes:
                            _, removed = self.entries.popitem(last=False)
                            self.size -= len(removed)
                        self.entries[key] = payload
                        self.size += len(payload)
            except BaseException as exc:
                flight.error = exc
            finally:
                with self.lock:
                    if self.flights.get(key) is flight:
                        self.flights.pop(key)
                    flight.done.set()
                monitor.join(timeout=0.1)
        else:
            while not flight.done.wait(0.02):
                if consumer.isCanceled():
                    with self.lock:
                        flight.consumers.remove(consumer)
                    raise CacheCancelled()
        with self.lock:
            if consumer in flight.consumers:
                flight.consumers.remove(consumer)
        if consumer.isCanceled() or self.closed or flight.epoch != self.epoch:
            raise CacheCancelled()
        if flight.error:
            raise flight.error
        return json.loads(flight.payload), "network" if leader else "shared"
