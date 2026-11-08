"""Structured logging: one event per line, as readable text or as JSON."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

logger = logging.getLogger("order_quality")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                 "level": record.levelname.lower(), "event": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(f"{k}={v}" for k, v in getattr(record, "fields", {}).items())
        return f"{record.levelname.lower():<7} {record.getMessage()}" + (f"  {fields}" if fields else "")


def configure(fmt: str = "text", level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _TextFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False


def event(message: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, message, extra={"fields": fields})
