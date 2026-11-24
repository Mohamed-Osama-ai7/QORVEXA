"""Immutable contracts for the in-process advisory workflow runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

WORKFLOW_SCHEMA: Final = "qorvexa-workflow/1"
MAX_TASKS: Final = 64
MAX_DEPENDENCIES: Final = 16
MAX_PAYLOAD_BYTES: Final = 65_536

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def encode_task_payload(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    # The encoder emits ASCII, so string length is the UTF-8 byte length.
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("Task payload exceeds the byte limit")
    return encoded


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class WorkflowScope(FrozenContract):
    """Tenant and one resource scope established by the calling service."""

    tenant_id: Identifier
    factory_id: Identifier | None = None
    asset_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_partition(self) -> WorkflowScope:
        if (self.factory_id is None) == (self.asset_id is None):
            raise ValueError("Workflow scope requires exactly one factory or asset ID")
        return self


class TaskSpec(FrozenContract):
    task_id: Identifier
    capability_id: Identifier
    capability_version: Identifier
    dependencies: tuple[Identifier, ...] = Field(default=(), max_length=MAX_DEPENDENCIES)


class WorkflowSpec(FrozenContract):
    schema_version: Literal["qorvexa-workflow/1"] = WORKFLOW_SCHEMA
    workflow_id: Identifier
    version: Identifier
    tasks: tuple[TaskSpec, ...] = Field(min_length=1, max_length=MAX_TASKS)


class ArtifactRef(FrozenContract):
    scope: WorkflowScope
    workflow_id: Identifier
    workflow_version: Identifier
    task_id: Identifier
    payload_sha256: Digest


class TaskArtifact(FrozenContract):
    """An immutable, validated JSON object snapshot with scoped provenance."""

    ref: ArtifactRef
    payload_json: str = Field(repr=False, max_length=MAX_PAYLOAD_BYTES)

    @model_validator(mode="after")
    def validate_snapshot(self) -> TaskArtifact:
        try:
            decoded = json.loads(self.payload_json)
            if not isinstance(decoded, dict) or encode_task_payload(decoded) != self.payload_json:
                raise ValueError("Artifact payload must be a canonical JSON object")
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Artifact payload must be a bounded canonical JSON object") from exc
        if hashlib.sha256(self.payload_json.encode()).hexdigest() != self.ref.payload_sha256:
            raise ValueError("Artifact payload digest does not match its reference")
        return self

    def read(self) -> dict[str, JsonValue]:
        """Return a fresh snapshot so consumers cannot alter another task's input."""
        return cast(dict[str, JsonValue], json.loads(self.payload_json))


class TaskStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskFailure(StrEnum):
    PERMISSION_DENIED = "permission_denied"
    INPUT_INVALID = "input_invalid"
    OUTPUT_INVALID = "output_invalid"
    HANDLER_FAILED = "handler_failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RETRIES_EXHAUSTED = "retries_exhausted"
    JOURNAL_UNAVAILABLE = "journal_unavailable"


class TaskResult(FrozenContract):
    task_id: Identifier
    status: TaskStatus
    artifact: TaskArtifact | None = None
    failure: TaskFailure | None = None
    blocked_by: tuple[Identifier, ...] = ()


class WorkflowResult(FrozenContract):
    scope: WorkflowScope
    workflow_id: Identifier
    version: Identifier
    tasks: tuple[TaskResult, ...]
