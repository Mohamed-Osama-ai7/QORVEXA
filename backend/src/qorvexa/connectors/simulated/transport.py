"""In-memory ``MqttTransport`` over a ``SimulatedFactory`` (SYNTHETIC; no sockets).

Delivers only frames matching the subscribed filters, can refuse connections, drop the connection
at chosen polls, and queue arbitrary raw messages to exercise malformed-input handling.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Collection, Sequence

from qorvexa.connectors.mqtt.transport import InboundMessage, TransportError
from qorvexa.connectors.simulated.factory import SimulatedFactory


def _matches(topic_filter: str, topic: str) -> bool:
    f, t = topic_filter.split("/"), topic.split("/")
    return len(f) == len(t) and all(a in ("+", b) for a, b in zip(f, t, strict=True))


class SimulatedMqttTransport:
    def __init__(
        self,
        factory: SimulatedFactory,
        *,
        ticks_per_poll: int = 1,
        fail_connects: int = 0,
        drop_at_polls: Collection[int] = (),
    ) -> None:
        self._factory = factory
        self._ticks = ticks_per_poll
        self._fail_connects = fail_connects
        self._drop_at = frozenset(drop_at_polls)
        self._connected = False
        self._filters: list[str] = []
        self._polls = 0
        self._raw: deque[InboundMessage] = deque()
        self.connect_calls = 0
        self.subscribed: list[tuple[str, int]] = []

    def inject_raw(self, topic: str, payload: bytes) -> None:
        self._raw.append(InboundMessage(topic, payload))

    # -- MqttTransport -----------------------------------------------------------------------

    def connect(self) -> None:
        self.connect_calls += 1
        if self._fail_connects > 0:
            self._fail_connects -= 1
            raise TransportError("connection_refused")
        self._connected = True

    def subscribe(self, topic_filter: str, qos: int) -> None:
        if not self._connected:
            raise TransportError("not_connected")
        self._filters.append(topic_filter)
        self.subscribed.append((topic_filter, qos))

    def poll(self, max_messages: int) -> Sequence[InboundMessage]:
        if not self._connected:
            raise TransportError("not_connected")
        self._polls += 1
        if self._polls in self._drop_at:
            self._connected = False
            self._filters.clear()
            raise TransportError("connection_lost")
        out: list[InboundMessage] = []
        for frame in self._factory.frames(self._ticks):
            if any(_matches(f, frame.topic) for f in self._filters):
                out.append(InboundMessage(frame.topic, json.dumps(frame.payload).encode()))
        while self._raw:
            msg = self._raw.popleft()
            if any(_matches(f, msg.topic) for f in self._filters):
                out.append(msg)
        return out[:max_messages]

    def close(self) -> None:
        self._connected = False
