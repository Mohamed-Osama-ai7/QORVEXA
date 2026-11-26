"""Correlation-ID propagation for logs and traces (stdlib ``contextvars`` only)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_CORRELATION_ID: ContextVar[str | None] = ContextVar("qorvexa_correlation_id", default=None)
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def sanitize_correlation_id(candidate: str | None) -> str:
    """Accept a caller-supplied ID only if it is short and log-safe; otherwise mint a new one."""
    if candidate is not None and _VALID.fullmatch(candidate):
        return candidate
    return new_correlation_id()


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of the block (async- and thread-safe)."""
    value = sanitize_correlation_id(correlation_id)
    token = _CORRELATION_ID.set(value)
    try:
        yield value
    finally:
        _CORRELATION_ID.reset(token)
