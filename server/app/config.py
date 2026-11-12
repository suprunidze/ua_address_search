"""Environment-only server configuration; secrets never appear in repr."""

from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    db_path: str = "./artifacts/uaas.sqlite3"
    global_daily: int = 1500
    token_daily: int = 100
    token_minute: int = 10
    timeout: float = 12.0

    @classmethod
    def from_env(cls):
        config = cls(
            api_key=os.environ.get("VISICOM_DATA_API_KEY", ""),
            db_path=os.environ.get("UAAS_DB_PATH", "./artifacts/uaas.sqlite3"),
            global_daily=int(os.environ.get("UAAS_GLOBAL_DAILY_LIMIT", "1500")),
            token_daily=int(os.environ.get("UAAS_TOKEN_DAILY_LIMIT", "100")),
            token_minute=int(os.environ.get("UAAS_TOKEN_MINUTE_LIMIT", "10")),
            timeout=float(os.environ.get("UAAS_UPSTREAM_TIMEOUT", "12")),
        )
        if min(config.global_daily, config.token_daily, config.token_minute, config.timeout) <= 0:
            raise ValueError("Server limits must be positive")
        return config
