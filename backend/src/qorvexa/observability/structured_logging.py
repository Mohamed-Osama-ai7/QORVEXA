"""Structured JSON logging with secret redaction.

Never logs credentials: any field whose *name* looks secret is replaced, and ``user:pass@`` URL
credentials are masked in string values.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Final

from qorvexa.observability.context import get_correlation_id

REDACTED: Final = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization|cookie|cert)",
    re.IGNORECASE,
)
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_STANDARD_ATTRS: Final = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

# Fields a caller may attach with ``extra={...}`` and which are promoted to top-level JSON keys.
CONTEXT_FIELDS: Final = (
    "component",
    "event_type",
    "factory_id",
    "connector_id",
    "error_category",
)


def redact_text(value: str) -> str:
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", value)


def redact(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SECRET_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v) for v in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value))


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str = "qorvexa-backend") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "severity": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        correlation_id = get_correlation_id()
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        for name, value in record.__dict__.items():
            if name in _STANDARD_ATTRS or name.startswith("_"):
                continue
            payload[name] = redact(value, key=name)
        if record.exc_info:
            # the exception class and a redacted message, never a raw traceback with local state
            exc_type = record.exc_info[0]
            payload["error_type"] = exc_type.__name__ if exc_type else "Exception"
            payload["error"] = redact_text(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=False)


def configure_json_logging(
    *, level: int = logging.INFO, service: str = "qorvexa-backend", logger_name: str = "qorvexa"
) -> logging.Logger:
    """Attach one JSON stream handler to the ``qorvexa`` logger tree (idempotent)."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in logger.handlers:
        if getattr(handler, "_qorvexa_json", False):
            handler.setFormatter(JsonFormatter(service))
            return logger
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    handler._qorvexa_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
    return logger
