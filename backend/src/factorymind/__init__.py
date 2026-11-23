"""Backward-compatibility bridge for legacy FactoryMind imports.

All modules and contracts have migrated to the canonical `qorvexa` namespace.
"""

from __future__ import annotations

import sys
import warnings

import qorvexa

warnings.warn(
    "The 'factorymind' package has been renamed to 'qorvexa'. "
    "Please update imports to 'import qorvexa' or 'from qorvexa import ...'.",
    DeprecationWarning,
    stacklevel=2,
)

_SUBMODULES = [
    "anomaly",
    "api",
    "assets",
    "auth",
    "config",
    "connectors",
    "contracts",
    "copilot",
    "data",
    "db",
    "decision",
    "evaluation",
    "knowledge",
    "ml",
    "twin",
]

for sub in _SUBMODULES:
    try:
        mod = __import__(f"qorvexa.{sub}", fromlist=["*"])
        sys.modules[f"factorymind.{sub}"] = mod
    except ImportError:
        pass

sys.modules["factorymind"] = qorvexa

__all__ = getattr(qorvexa, "__all__", [])
