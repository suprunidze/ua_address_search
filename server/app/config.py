"""Environment-only server configuration; secrets never appear in repr."""

from dataclasses import dataclass, field
import os
import json
import re


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    db_path: str = "./artifacts/uaas.sqlite3"
    global_daily: int = 1500
    token_daily: int = 100
    token_minute: int = 10
    timeout: float = 12.0
    token_daily_overrides: dict = field(default_factory=dict, repr=False)
    registration_enabled: bool = True
    registration_minute: int = 10
    registration_daily: int = 100

    @classmethod
    def from_env(cls):
        config = cls(
            api_key=os.environ.get("VISICOM_DATA_API_KEY", ""),
            db_path=os.environ.get("UAAS_DB_PATH", "./artifacts/uaas.sqlite3"),
            global_daily=int(os.environ.get("UAAS_GLOBAL_DAILY_LIMIT", "1500")),
            token_daily=int(os.environ.get("UAAS_TOKEN_DAILY_LIMIT", "100")),
            token_minute=int(os.environ.get("UAAS_TOKEN_MINUTE_LIMIT", "10")),
            timeout=float(os.environ.get("UAAS_UPSTREAM_TIMEOUT", "12")),
            token_daily_overrides=json.loads(
                os.environ.get("UAAS_TOKEN_DAILY_LIMIT_OVERRIDES", "{}")
            ),
            registration_enabled=os.environ.get("UAAS_REGISTRATION_ENABLED", "true").lower()
            in ("true", "1", "yes"),
            registration_minute=int(os.environ.get("UAAS_REGISTRATION_MINUTE_LIMIT", "10")),
            registration_daily=int(os.environ.get("UAAS_REGISTRATION_DAILY_LIMIT", "100")),
        )
        if min(config.global_daily, config.token_daily, config.token_minute, config.timeout) <= 0:
            raise ValueError("Server limits must be positive")
        if min(config.registration_minute, config.registration_daily) <= 0:
            raise ValueError("Registration limits must be positive")
        overrides = config.token_daily_overrides
        if not isinstance(overrides, dict) or any(
            not isinstance(key, str)
            or not re.fullmatch(r"[a-f0-9]{64}", key)
            or type(value) is not int
            or value <= 0
            for key, value in overrides.items()
        ):
            raise ValueError("Invalid token daily limit overrides")
        return config
