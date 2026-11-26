"""In-memory SYNTHETIC OPC UA server over a ``SimulatedFactory`` (no sockets, no library).

Address space: ``i=85`` (Objects) -> ``ns=2;s=<factory>`` -> ``ns=2;s=<factory>.<equipment>`` ->
variables ``ns=2;s=<factory>.<equipment>.<tag>``. Failure modes: refused connections, connection
loss, per-node access denial, unknown nodes, a hung source (server up, data frozen), a missing
source timestamp, and the factory's own data faults mapped to status codes. It exposes only the
read-only ``OpcUaTransport`` operations.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from qorvexa.connectors.opcua import status
from qorvexa.connectors.opcua.transport import BrowseEntry, NodeReading, OpcUaTransportError
from qorvexa.connectors.simulated.factory import UNITS, SimulatedFactory

OBJECTS = "i=85"
NAMESPACES = ("http://opcfoundation.org/UA/", "urn:qorvexa:simulated")


def opcua_node_id(factory_id: str, equipment_id: str | None = None, tag: str | None = None) -> str:
    parts = [p for p in (factory_id, equipment_id, tag) if p is not None]
    return "ns=2;s=" + ".".join(parts)


@dataclass
class _Node:
    value: object = None
    status_code: int = status.BAD_NO_DATA
    timestamp: datetime | None = None


class SimulatedOpcUaTransport:
    def __init__(
        self,
        factory: SimulatedFactory,
        *,
        fail_connects: int = 0,
        drop_at_calls: Collection[int] = (),
        denied_nodes: Collection[str] = (),
        freeze_after: int | None = None,
        omit_source_timestamp: bool = False,
    ) -> None:
        self._factory = factory
        self._fail_connects = fail_connects
        self._drop_at = frozenset(drop_at_calls)
        self._denied = frozenset(denied_nodes)
        self._freeze_after = freeze_after
        self._omit_source_ts = omit_source_timestamp
        self._connected = False
        self._data_calls = 0
        self._advances = 0
        self._nodes: dict[str, _Node] = {}
        self._subscription: tuple[tuple[str, ...], int] | None = None
        self._delivered: dict[str, tuple[object, int, datetime | None]] = {}
        self.connect_calls = 0
        self.subscriptions_created = 0
        self.subscription_deleted = 0
        self.read_calls = 0
        # the address space is static; only values change over time
        self._machines: list[str] = list(factory.machine_ids)
        self._tags: dict[str, list[str]] = {m: list(UNITS) for m in self._machines}

    # -- helpers -----------------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise OpcUaTransportError("not_connected")

    def _maybe_drop(self) -> None:
        self._data_calls += 1
        if self._data_calls in self._drop_at:
            self._connected = False
            self._subscription = None
            raise OpcUaTransportError("connection_lost")

    def _advance(self) -> None:
        if self._freeze_after is not None and self._advances >= self._freeze_after:
            return
        self._advances += 1
        for frame in self._factory.frames(1):
            p = frame.payload
            ts = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
            eq = p["equipment_id"]
            frame_bad = p.get("quality") == "BAD"
            for tag, item in p["measurements"].items():
                node = self._nodes.setdefault(opcua_node_id(p["factory_id"], eq, tag), _Node())
                if node.timestamp is not None and ts < node.timestamp:
                    continue  # a server holds the newest value; late frames never roll it back
                value = item.get("value")
                node.timestamp = ts
                node.value = value
                if value is None:
                    node.status_code = status.BAD_NO_DATA
                elif frame_bad:
                    node.status_code = 0x80000000
                else:
                    node.status_code = status.GOOD

    def _reading(self, node_id: str) -> NodeReading:
        if node_id in self._denied:
            return NodeReading(node_id, None, status.BAD_USER_ACCESS_DENIED)
        node = self._nodes.get(node_id)
        if node is None:
            return NodeReading(node_id, None, status.BAD_NODE_ID_UNKNOWN)
        return NodeReading(
            node_id,
            node.value,
            node.status_code,
            None if self._omit_source_ts else node.timestamp,
            node.timestamp,
        )

    # -- OpcUaTransport ----------------------------------------------------------------------

    def connect(self) -> None:
        self.connect_calls += 1
        if self._fail_connects > 0:
            self._fail_connects -= 1
            raise OpcUaTransportError("connection_refused")
        self._connected = True  # time only advances with reads, never on connect

    def close(self) -> None:
        self._connected = False
        self._subscription = None

    def namespaces(self) -> Sequence[str]:
        self._require_connected()
        return NAMESPACES

    def browse(self, node_id: str) -> Sequence[BrowseEntry]:
        self._require_connected()
        fid = self._factory.factory_id
        if node_id == OBJECTS:
            return [BrowseEntry(opcua_node_id(fid), fid, False)]
        if node_id == opcua_node_id(fid):
            return [BrowseEntry(opcua_node_id(fid, m), m, False) for m in self._machines]
        for m in self._machines:
            if node_id == opcua_node_id(fid, m):
                return [BrowseEntry(opcua_node_id(fid, m, t), t, True) for t in self._tags[m]]
        return []

    def read(self, node_ids: Sequence[str]) -> Sequence[NodeReading]:
        self._require_connected()
        self._maybe_drop()
        self.read_calls += 1
        self._advance()
        return [self._reading(n) for n in node_ids]

    def create_subscription(self, node_ids: Sequence[str], interval_ms: int) -> None:
        self._require_connected()
        self.subscriptions_created += 1
        self._subscription = (tuple(node_ids), interval_ms)
        self._delivered.clear()

    def poll_changes(self, max_items: int) -> Sequence[NodeReading]:
        self._require_connected()
        self._maybe_drop()
        if self._subscription is None:
            raise OpcUaTransportError("no_subscription")
        self._advance()
        out: list[NodeReading] = []
        for node_id in self._subscription[0]:
            r = self._reading(node_id)
            fingerprint = (r.value, r.status_code, r.source_timestamp or r.server_timestamp)
            if self._delivered.get(node_id) != fingerprint:
                self._delivered[node_id] = fingerprint
                out.append(r)
        return out[:max_items]

    def delete_subscription(self) -> None:
        self._require_connected()
        self.subscription_deleted += 1
        self._subscription = None
