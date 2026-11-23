"""Settings isolation used by the autouse ``_hermetic_settings`` fixture in ``conftest.py``.

Kept in its own module so the teardown behaviour can be tested directly."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from qorvexa.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

ENV_PREFIXES = ("QORVEXA_", "FACTORYMIND_")
ENV_PREFIX = "QORVEXA_"


@contextmanager
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Disable ``.env`` loading and hide ambient env variables for the duration.

    Hides both ``QORVEXA_*`` and ``FACTORYMIND_*`` variables.
    The ``get_settings`` cache is cleared on entry and on exit, including when the body raises,
    so settings built inside the scope never outlive it. Environment variables and
    ``Settings.model_config["env_file"]`` are restored by ``monkeypatch.undo()``."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if any(name.upper().startswith(p) for p in ENV_PREFIXES):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
