"""Explicit stdout JSON events; callers pass only allowlisted, non-secret fields."""

from datetime import datetime, timezone
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record):
        value = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        value.update(getattr(record, "fields", {}))
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def configure():
    logger = logging.getLogger("uaas")
    if not any(getattr(handler, "uaas_stdout", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.uaas_stdout = True
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
