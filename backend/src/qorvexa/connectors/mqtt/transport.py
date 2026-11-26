"""Read-only MQTT transport protocol.

Deliberately has **no publish method**. A real adapter (for example around Eclipse Paho, added once
the dependency is approved) implements only these four operations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class TransportError(Exception):
    """Connection-level failure. ``category`` is a short, secret-free label used in logs/metrics."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class InboundMessage:
    topic: str
    payload: bytes
    qos: int = 0


class MqttTransport(Protocol):
    def connect(self) -> None: ...

    def subscribe(self, topic_filter: str, qos: int) -> None: ...

    def poll(self, max_messages: int) -> Sequence[InboundMessage]: ...

    def close(self) -> None: ...
