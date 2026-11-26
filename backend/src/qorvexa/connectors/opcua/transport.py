"""Read-only OPC UA transport protocol.

Offers browse, read and subscription *reading* only. There is deliberately no write, method-call,
node-management or history-update operation. A real adapter (for example around ``asyncua``, added
once the dependency is approved) implements only these operations, and should additionally use a
server-side read-only account.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class OpcUaTransportError(Exception):
    """Connection-level failure. ``category`` is a short, secret-free label for logs/metrics."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True)
class BrowseEntry:
    node_id: str
    display_name: str
    is_variable: bool


@dataclass(frozen=True)
class NodeReading:
    node_id: str
    value: object
    status_code: int
    source_timestamp: datetime | None = None
    server_timestamp: datetime | None = None


class OpcUaTransport(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def namespaces(self) -> Sequence[str]: ...

    def browse(self, node_id: str) -> Sequence[BrowseEntry]: ...

    def read(self, node_ids: Sequence[str]) -> Sequence[NodeReading]: ...

    def create_subscription(self, node_ids: Sequence[str], interval_ms: int) -> None: ...

    def poll_changes(self, max_items: int) -> Sequence[NodeReading]: ...

    def delete_subscription(self) -> None: ...
