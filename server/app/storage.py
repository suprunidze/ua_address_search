"""Transactional token and quota storage for a single Railway replica."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
from pathlib import Path
import secrets
import sqlite3
from typing import Iterator, Optional
from zoneinfo import ZoneInfo


class AccessError(Exception):
    def __init__(self, code: str, status: int = 429, retry_after: int = 60):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__(code)


class Store:
    def __init__(self, path: str, daily: int = 1500, per_token: int = 100, per_minute: int = 10):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily = daily
        self.per_token = per_token
        self.per_minute = per_minute
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tokens (
                    hash TEXT PRIMARY KEY, label TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS daily (
                    day TEXT NOT NULL, scope TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(day, scope));
                CREATE TABLE IF NOT EXISTS minute (
                    bucket TEXT NOT NULL, hash TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(bucket, hash));
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, label: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("INSERT INTO tokens(hash,label) VALUES (?,?)", (self.digest(token), label))
        return token

    def revoke(self, label: str) -> int:
        with self.connection() as db:
            return db.execute("UPDATE tokens SET revoked=1 WHERE label=?", (label,)).rowcount

    def authenticate(self, token: str) -> str:
        digest = self.digest(token)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM tokens WHERE hash=? AND revoked=0", (digest,)).fetchone():
                return digest
        raise AccessError("unauthorized", 401)

    @staticmethod
    def clock(now: Optional[datetime] = None):
        current = now or datetime.now(ZoneInfo("Europe/Kyiv"))
        if current.tzinfo is None:
            raise ValueError("A timezone-aware datetime is required")
        current = current.astimezone(ZoneInfo("Europe/Kyiv"))
        return current.date().isoformat(), str(int(current.timestamp()) // 60)

    def reserve(self, digest: str, upstream: bool = True, now: Optional[datetime] = None) -> None:
        day, minute = self.clock(now)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if not db.execute(
                    "SELECT 1 FROM tokens WHERE hash=? AND revoked=0", (digest,)
                ).fetchone():
                    raise AccessError("unauthorized", 401)
                row = db.execute(
                    "SELECT count FROM minute WHERE bucket=? AND hash=?", (minute, digest)
                ).fetchone()
                if row and row[0] >= self.per_minute:
                    raise AccessError("rate_limit")
                if upstream:
                    for scope, limit in (("global", self.daily), (digest, self.per_token)):
                        row = db.execute(
                            "SELECT count FROM daily WHERE day=? AND scope=?", (day, scope)
                        ).fetchone()
                        if row and row[0] >= limit:
                            raise AccessError("daily_quota", retry_after=3600)
                    for scope in ("global", digest):
                        db.execute(
                            "INSERT INTO daily VALUES (?,?,1) ON CONFLICT(day,scope) "
                            "DO UPDATE SET count=count+1",
                            (day, scope),
                        )
                db.execute(
                    "INSERT INTO minute VALUES (?,?,1) ON CONFLICT(bucket,hash) "
                    "DO UPDATE SET count=count+1",
                    (minute, digest),
                )
                db.execute("DELETE FROM minute WHERE bucket < ?", (str(int(minute) - 2),))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def quota(self, digest: str, now: Optional[datetime] = None):
        day, _ = self.clock(now)
        with self.connection() as db:
            values = dict(db.execute("SELECT scope,count FROM daily WHERE day=?", (day,)))
        return {
            "day": day,
            "timezone": "Europe/Kyiv",
            "remaining": max(
                0, min(self.daily - values.get("global", 0), self.per_token - values.get(digest, 0))
            ),
            "token_daily_limit": self.per_token,
        }
