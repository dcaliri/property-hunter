"""JSON-formatted structured logging (constitution I: observability)."""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = run_id
        stage = getattr(record, "stage", None)
        if stage is not None:
            payload["stage"] = stage
        for key, value in record.__dict__.items():
            if key.startswith("ctx_"):
                payload[key[4:]] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", run_id: int | None = None) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    logger = logging.getLogger("property_hunter")
    logger.setLevel(level.upper())
    if run_id is not None:
        logger.setLevel(logging.DEBUG)
    return logger


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
