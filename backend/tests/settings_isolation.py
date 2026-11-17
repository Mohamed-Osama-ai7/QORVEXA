"""Settings isolation used by the autouse ``_hermetic_settings`` fixture in ``conftest.py``.

Kept in its own module so the teardown behaviour can be tested directly."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from factorymind.config import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

ENV_PREFIX = "FACTORYMIND_"


@contextmanager
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Disable ``.env`` loading and hide ``FACTORYMIND_*`` variables for the duration.

    The ``get_settings`` cache is cleared on entry and on exit, including when the body raises,
    so settings built inside the scope never outlive it. Environment variables and
    ``Settings.model_config["env_file"]`` are restored by ``monkeypatch.undo()``."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()
