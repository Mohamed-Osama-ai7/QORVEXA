"""Regression tests for the settings isolation behind the autouse ``_hermetic_settings`` fixture."""

from __future__ import annotations

import os

import pytest

from factorymind.config import Settings, get_settings
from tests.settings_isolation import hermetic_settings

AMBIENT = "FACTORYMIND_ENVIRONMENT"


def test_autouse_fixture_is_active_for_every_test() -> None:
    assert Settings.model_config["env_file"] is None
    assert not [name for name in os.environ if name.upper().startswith("FACTORYMIND_")]
    assert get_settings().environment == "development"


def test_scope_hides_ambient_settings_and_restores_them_on_undo() -> None:
    with pytest.MonkeyPatch.context() as outer:
        outer.setenv(AMBIENT, "production")
        outer.setenv("factorymind_lowercase_var", "x")
        outer.setitem(Settings.model_config, "env_file", "sentinel.env")
        get_settings.cache_clear()

        inner = pytest.MonkeyPatch()
        try:
            with hermetic_settings(inner):
                assert AMBIENT not in os.environ
                assert "factorymind_lowercase_var" not in {k.lower() for k in os.environ}
                assert Settings.model_config["env_file"] is None
                assert get_settings().environment == "development"
                assert get_settings.cache_info().currsize == 1
            assert get_settings.cache_info().currsize == 0
        finally:
            inner.undo()

        assert os.environ[AMBIENT] == "production"
        assert Settings.model_config["env_file"] == "sentinel.env"
        assert get_settings().environment == "production"
        get_settings.cache_clear()


def test_scope_clears_cache_on_entry() -> None:
    stale = get_settings()
    inner = pytest.MonkeyPatch()
    try:
        with hermetic_settings(inner):
            assert get_settings.cache_info().currsize == 0
            assert get_settings() is not stale
    finally:
        inner.undo()


def test_scope_clears_cache_when_body_raises() -> None:
    inner = pytest.MonkeyPatch()
    try:
        with pytest.raises(RuntimeError, match="boom"), hermetic_settings(inner):
            get_settings()
            raise RuntimeError("boom")
        assert get_settings.cache_info().currsize == 0
    finally:
        inner.undo()
