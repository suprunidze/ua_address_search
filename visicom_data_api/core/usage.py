"""Atomic, computer-local estimates; never enforce the provider's quota."""

import hashlib
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


class LocalUsage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage "
                "(day TEXT, identity TEXT, count INTEGER NOT NULL, PRIMARY KEY(day,identity))"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, key: str, day: str, limit: int, increment: bool = True) -> dict:
        identity = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if increment:
                db.execute(
                    "INSERT INTO usage VALUES (?,?,1) ON CONFLICT(day,identity) "
                    "DO UPDATE SET count=count+1",
                    (day, identity),
                )
            row = db.execute(
                "SELECT count FROM usage WHERE day=? AND identity=?",
                (day, identity),
            ).fetchone()
            used = row[0] if row else 0
            observed_at = time.time_ns()
        return {
            "day": day,
            "observed_at": observed_at,
            "used": used,
            "remaining": max(0, limit - used),
            "token_daily_limit": limit,
            "local": True,
            "exceeded": max(0, used - limit),
        }
