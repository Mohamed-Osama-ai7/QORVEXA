"""Deterministic SYNTHETIC factory telemetry for testing read-only connectors. Not plant data."""

from qorvexa.connectors.simulated.factory import (
    FaultProfile,
    Frame,
    SimulatedFactory,
    SimulatedMachine,
    mqtt_topic,
)
from qorvexa.connectors.simulated.transport import SimulatedMqttTransport

__all__ = [
    "FaultProfile",
    "Frame",
    "SimulatedFactory",
    "SimulatedMachine",
    "SimulatedMqttTransport",
    "mqtt_topic",
]
