"""Resolve an advisory intent against application-owned workflow templates."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from pydantic import BaseModel, JsonValue

from qorvexa.agents.capabilities import CapabilityRegistry
from qorvexa.agents.contracts import (
    WorkflowResult,
    WorkflowScope,
    WorkflowSpec,
    encode_task_payload,
)
from qorvexa.agents.runtime import Supervisor
from qorvexa.auth.principal import Permission


class RouteScope(StrEnum):
    FACTORY = "factory"
    ASSET = "asset"


class RoutingFailure(StrEnum):
    UNKNOWN_ROUTE = "unknown_route"
    WRONG_SCOPE = "wrong_scope"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INTAKE = "invalid_intake"


class WorkflowRoutingError(ValueError):
    def __init__(self, failure: RoutingFailure) -> None:
        super().__init__(failure.value)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class WorkflowRoute:
    workflow: WorkflowSpec
    scope: RouteScope
    intake_task_id: str


@dataclass(frozen=True, slots=True)
class PreparedWorkflow:
    """A versioned template and intake snapshot authorized by the calling service."""

    workflow: WorkflowSpec
    scope: WorkflowScope
    permissions: frozenset[Permission]
    intake_task_id: str
    _arguments_json: str = field(repr=False)

    def inputs(self) -> dict[str, dict[str, JsonValue]]:
        return {self.intake_task_id: cast(dict[str, JsonValue], json.loads(self._arguments_json))}


@dataclass(frozen=True, slots=True)
class _RegisteredRoute:
    template: WorkflowRoute
    input_model: type[BaseModel]
    required_permissions: frozenset[Permission]


class WorkflowRouter:
    """Accept arguments for a predefined graph with a single designated intake task."""

    def __init__(self, registry: CapabilityRegistry, routes: Iterable[WorkflowRoute]) -> None:
        self._supervisor = Supervisor(registry)
        entries: dict[str, _RegisteredRoute] = {}
        for route in routes:
            workflow = route.workflow
            if workflow.workflow_id in entries:
                raise ValueError(f"Multiple templates for workflow {workflow.workflow_id}")
            if not isinstance(route.scope, RouteScope):
                raise ValueError("Route scope must be declared")
            self._supervisor.validate(workflow)
            if not any(
                task.task_id == route.intake_task_id and not task.dependencies
                for task in workflow.tasks
            ):
                raise ValueError("Route intake must name a root task")
            permissions: set[Permission] = set()
            input_model: type[BaseModel] | None = None
            for task in workflow.tasks:
                capability = registry.get(task.capability_id, task.capability_version)
                if capability is None:
                    raise ValueError("Route references an unknown capability")
                permissions.update(capability.required_permissions)
                if task.task_id == route.intake_task_id:
                    input_model = capability.input_model
                else:
                    try:
                        capability.input_model.model_validate_json(
                            "{}", strict=True, extra="forbid"
                        )
                    except Exception as exc:
                        raise ValueError(
                            "Only the intake task may require request arguments"
                        ) from exc
            if input_model is None:
                raise ValueError("Route intake capability schema is missing")
            entries[workflow.workflow_id] = _RegisteredRoute(
                route, input_model, frozenset(permissions)
            )
        self._routes = MappingProxyType(entries)

    def prepare(
        self,
        workflow_id: str,
        *,
        scope: WorkflowScope,
        arguments: Mapping[str, JsonValue],
        permissions: frozenset[Permission],
    ) -> PreparedWorkflow:
        """Apply trusted caller scope and permissions before any task can start."""
        route = self._routes.get(workflow_id)
        if route is None:
            raise WorkflowRoutingError(RoutingFailure.UNKNOWN_ROUTE)
        scope_kind = RouteScope.ASSET if scope.asset_id is not None else RouteScope.FACTORY
        if scope_kind is not route.template.scope:
            raise WorkflowRoutingError(RoutingFailure.WRONG_SCOPE)
        if not route.required_permissions <= permissions:
            raise WorkflowRoutingError(RoutingFailure.PERMISSION_DENIED)

        try:
            snapshot = encode_task_payload(arguments)
            route.input_model.model_validate_json(snapshot, strict=True, extra="forbid")
        except Exception:
            raise WorkflowRoutingError(RoutingFailure.INVALID_INTAKE) from None

        return PreparedWorkflow(
            route.template.workflow,
            scope,
            frozenset(permissions),
            route.template.intake_task_id,
            snapshot,
        )

    def run(
        self,
        workflow_id: str,
        *,
        scope: WorkflowScope,
        arguments: Mapping[str, JsonValue],
        permissions: frozenset[Permission],
    ) -> WorkflowResult:
        prepared = self.prepare(
            workflow_id, scope=scope, arguments=arguments, permissions=permissions
        )
        return self._supervisor.run(
            prepared.workflow,
            scope=prepared.scope,
            inputs=prepared.inputs(),
            permissions=prepared.permissions,
        )
