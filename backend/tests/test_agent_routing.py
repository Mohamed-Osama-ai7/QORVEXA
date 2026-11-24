"""Routing admits only trusted graphs, caller scopes, and typed intake."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field, JsonValue

from qorvexa.agents import Capability, CapabilityRegistry, TaskContext, TaskSpec, TaskStatus
from qorvexa.agents.contracts import WorkflowScope, WorkflowSpec
from qorvexa.agents.execution import ExecutionService
from qorvexa.agents.routing import RouteScope, RoutingFailure, WorkflowRoute, WorkflowRouter
from qorvexa.agents.routing import WorkflowRoutingError as RoutingError
from qorvexa.agents.run_store import RunState, RunStore
from qorvexa.auth.principal import Permission


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=100)


class Empty(BaseModel):
    pass


SCOPE = WorkflowScope(tenant_id="tenant", asset_id="compressor")
REVIEW_SCOPE = WorkflowScope(tenant_id="tenant", factory_id="plant")
PERMISSIONS = frozenset({Permission.ASSET_READ, Permission.AUDIT_READ})
INTAKE = TaskSpec(task_id="intake", capability_id="intake", capability_version="1")
REVIEW = TaskSpec(
    task_id="review", capability_id="review", capability_version="1", dependencies=("intake",)
)
ASSET_ROUTE = WorkflowRoute(
    WorkflowSpec(workflow_id="asset-evidence", version="v2", tasks=(INTAKE,)),
    RouteScope.ASSET,
    "intake",
)
FACTORY_ROUTE = WorkflowRoute(
    WorkflowSpec(workflow_id="factory-review", version="v3", tasks=(REVIEW, INTAKE)),
    RouteScope.FACTORY,
    "intake",
)


def make_registry(calls: list[str]) -> CapabilityRegistry:
    def intake(request: Question, context: TaskContext) -> Question:
        calls.append(context.task_id)
        return request

    def review(request: Empty, context: TaskContext) -> Question:
        calls.append(context.task_id)
        return Question.model_validate(context.dependency("intake").read())

    return CapabilityRegistry(
        (
            Capability.register(
                "intake",
                "1",
                input_model=Question,
                output_model=Question,
                handler=intake,
                required_permissions=frozenset({Permission.ASSET_READ}),
            ),
            Capability.register(
                "review",
                "1",
                input_model=Empty,
                output_model=Question,
                handler=review,
                required_permissions=frozenset({Permission.AUDIT_READ}),
            ),
        )
    )


def test_router_uses_registered_graph_version_and_preserves_scoped_artifacts() -> None:
    calls: list[str] = []
    router = WorkflowRouter(make_registry(calls), (ASSET_ROUTE, FACTORY_ROUTE))
    asset = router.run(
        "asset-evidence",
        scope=SCOPE,
        arguments={"question": "Air leak"},
        permissions=frozenset({Permission.ASSET_READ}),
    )
    factory = router.run(
        "factory-review",
        scope=REVIEW_SCOPE,
        arguments={"question": "Audit evidence"},
        permissions=PERMISSIONS,
    )

    assert (asset.workflow_id, asset.version) == ("asset-evidence", "v2")
    assert (factory.workflow_id, factory.version) == ("factory-review", "v3")
    assert calls == ["intake", "intake", "review"]
    assert all(task.status is TaskStatus.SUCCEEDED for task in factory.tasks)
    artifact = factory.tasks[1].artifact
    assert artifact is not None
    assert artifact.ref.scope == REVIEW_SCOPE
    assert artifact.read() == {"question": "Audit evidence"}


@pytest.mark.parametrize(
    ("route", "scope", "arguments", "permissions", "failure"),
    [
        ("approval", SCOPE, {"question": "Review"}, PERMISSIONS, RoutingFailure.UNKNOWN_ROUTE),
        ("factory-review", SCOPE, {"question": "Review"}, PERMISSIONS, RoutingFailure.WRONG_SCOPE),
        (
            "factory-review",
            REVIEW_SCOPE,
            {"question": "Review"},
            frozenset({Permission.ASSET_READ}),
            RoutingFailure.PERMISSION_DENIED,
        ),
        ("asset-evidence", SCOPE, {"question": 1}, PERMISSIONS, RoutingFailure.INVALID_INTAKE),
        ("asset-evidence", SCOPE, {}, PERMISSIONS, RoutingFailure.INVALID_INTAKE),
        (
            "asset-evidence",
            SCOPE,
            {"question": "x" * 65536},
            PERMISSIONS,
            RoutingFailure.INVALID_INTAKE,
        ),
        (
            "asset-evidence",
            SCOPE,
            {"question": "Review", "tenant_id": "other"},
            PERMISSIONS,
            RoutingFailure.INVALID_INTAKE,
        ),
    ],
)
def test_routing_refusal_executes_no_tasks(
    route: str,
    scope: WorkflowScope,
    arguments: dict[str, JsonValue],
    permissions: frozenset[Permission],
    failure: RoutingFailure,
) -> None:
    calls: list[str] = []
    router = WorkflowRouter(make_registry(calls), (ASSET_ROUTE, FACTORY_ROUTE))
    with pytest.raises(RoutingError) as error:
        router.run(route, scope=scope, arguments=arguments, permissions=permissions)
    assert error.value.failure is failure
    assert str(error.value) == failure.value
    assert calls == []


def test_route_configuration_rejects_ambiguous_ids_and_nonroot_intake() -> None:
    registry = make_registry([])
    with pytest.raises(ValueError, match="Multiple templates"):
        WorkflowRouter(registry, (ASSET_ROUTE, ASSET_ROUTE))
    only_review_as_intake = WorkflowRoute(FACTORY_ROUTE.workflow, RouteScope.FACTORY, "review")
    with pytest.raises(ValueError, match="root task"):
        WorkflowRouter(registry, (only_review_as_intake,))


def test_only_the_root_intake_task_can_require_request_arguments() -> None:
    second_input = TaskSpec(
        task_id="second", capability_id="intake", capability_version="1", dependencies=("intake",)
    )
    route = WorkflowRoute(
        WorkflowSpec(workflow_id="multiple-inputs", version="1", tasks=(second_input, INTAKE)),
        RouteScope.ASSET,
        "intake",
    )
    with pytest.raises(ValueError, match="Only the intake task"):
        WorkflowRouter(make_registry([]), (route,))


def test_prepared_route_uses_the_same_approved_inputs_in_durable_execution(tmp_path: Path) -> None:
    calls: list[str] = []
    registry = make_registry(calls)
    router = WorkflowRouter(registry, (ASSET_ROUTE,))
    arguments: dict[str, JsonValue] = {"question": "Private evidence question"}
    prepared = router.prepare(
        "asset-evidence", scope=SCOPE, arguments=arguments, permissions=PERMISSIONS
    )
    arguments["question"] = "Changed after authorization"
    assert "Private evidence question" not in repr(prepared)
    assert calls == []

    execution = ExecutionService(registry, RunStore(tmp_path / "runs.sqlite"))
    record = execution.run(
        prepared.workflow,
        scope=prepared.scope,
        permissions=prepared.permissions,
        inputs=prepared.inputs(),
        idempotency_key="request-1",
    )
    assert record.state is RunState.COMPLETED and record.result is not None
    assert record.result.version == "v2" and record.scope == SCOPE
    artifact = record.result.tasks[0].artifact
    assert artifact is not None
    assert artifact.read() == {"question": "Private evidence question"}
    assert calls == ["intake"]
