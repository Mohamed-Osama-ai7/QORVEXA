"""Trusted registration of typed, advisory capabilities."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast

from pydantic import BaseModel, RootModel, TypeAdapter

from qorvexa.agents.contracts import Identifier, TaskArtifact, WorkflowScope
from qorvexa.auth.principal import Permission

_IDENTIFIER: Final[TypeAdapter[str]] = TypeAdapter(Identifier)
_ADVISORY_PERMISSIONS: Final = frozenset(
    {
        Permission.ANOMALY_READ,
        Permission.ASSET_READ,
        Permission.INCIDENT_READ,
        Permission.AUDIT_READ,
    }
)


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Only validated direct dependency outputs are visible to a handler."""

    scope: WorkflowScope
    workflow_id: str
    workflow_version: str
    task_id: str
    dependencies: tuple[TaskArtifact, ...]

    def dependency(self, task_id: str) -> TaskArtifact:
        for artifact in self.dependencies:
            if artifact.ref.task_id == task_id:
                return artifact
        raise KeyError(task_id)


@dataclass(frozen=True, slots=True)
class Capability:
    """Binding of a versioned handler to strict schemas and read permissions."""

    capability_id: str
    version: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel, TaskContext], BaseModel]
    required_permissions: frozenset[Permission]

    def __post_init__(self) -> None:
        _IDENTIFIER.validate_python(self.capability_id, strict=True)
        _IDENTIFIER.validate_python(self.version, strict=True)
        if any(issubclass(schema, RootModel) for schema in (self.input_model, self.output_model)):
            raise ValueError("Capability schemas must describe JSON objects")
        required = frozenset(self.required_permissions)
        if not required <= _ADVISORY_PERMISSIONS:
            raise ValueError("Workflow capabilities may require only advisory read permissions")
        object.__setattr__(self, "required_permissions", required)

    @staticmethod
    def register[InputT: BaseModel, OutputT: BaseModel](
        capability_id: str,
        version: str,
        *,
        input_model: type[InputT],
        output_model: type[OutputT],
        handler: Callable[[InputT, TaskContext], OutputT],
        required_permissions: frozenset[Permission] = frozenset(),
    ) -> Capability:
        def invoke(value: BaseModel, context: TaskContext) -> BaseModel:
            return handler(cast(InputT, value), context)

        return Capability(
            capability_id, version, input_model, output_model, invoke, required_permissions
        )


@dataclass(frozen=True, slots=True, init=False)
class CapabilityRegistry:
    """Immutable allowlist supplied by trusted application code."""

    _by_key: Mapping[tuple[str, str], Capability]

    def __init__(self, capabilities: Iterable[Capability]) -> None:
        entries: dict[tuple[str, str], Capability] = {}
        for capability in capabilities:
            key = capability.capability_id, capability.version
            if key in entries:
                raise ValueError(f"Duplicate capability registration: {key[0]}@{key[1]}")
            entries[key] = capability
        object.__setattr__(self, "_by_key", MappingProxyType(entries))

    def get(self, capability_id: str, version: str) -> Capability | None:
        return self._by_key.get((capability_id, version))
