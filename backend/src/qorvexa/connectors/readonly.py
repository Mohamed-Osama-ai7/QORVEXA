"""Structural read-only guard for industrial client objects.

Industrial clients (OPC UA, MQTT, historian) are wrapped behind small read-only protocols. As a
second line of defence, ``ensure_read_only`` refuses any object that exposes a write/control style
method, so a full-featured library client can never be handed to a connector directly.
"""

from __future__ import annotations

from typing import Final

FORBIDDEN_METHOD_NAMES: Final = frozenset(
    {
        "publish",
        "write",
        "write_value",
        "write_values",
        "set_value",
        "set_values",
        "call_method",
        "call",
        "add_nodes",
        "delete_nodes",
        "history_update",
        "send",
        "put",
        "post",
        "delete",
        "command",
        "actuate",
    }
)


class ReadOnlyViolationError(RuntimeError):
    """A client or transport exposes a write/control capability."""


def ensure_read_only(client: object) -> None:
    exposed = sorted(
        name
        for name in FORBIDDEN_METHOD_NAMES
        if callable(getattr(client, name, None)) and not name.startswith("_")
    )
    if exposed:
        raise ReadOnlyViolationError(
            f"{type(client).__name__} exposes write/control methods: {', '.join(exposed)}"
        )
