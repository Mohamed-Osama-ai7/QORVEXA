"""Backward-compatibility forwarding to qorvexa.config."""

from __future__ import annotations

from qorvexa.config import IDENTIFIER, Settings, get_settings

__all__ = ["IDENTIFIER", "Settings", "get_settings"]
