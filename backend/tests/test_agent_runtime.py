"""Hermetic contracts and invariants for the advisory workflow runtime."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest
from pydantic import BaseModel, ValidationError

from qorvexa.agents import (
    ArtifactRef,
    Capability,
    CapabilityRegistry,
    Supervisor,
    TaskArtifact,
    TaskContext,
    TaskFailure,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
    WorkflowSpec,
    WorkflowValidationError,
)
from qorvexa.agents.contracts import MAX_PAYLOAD_BYTES, MAX_TASKS
from qorvexa.auth.principal import Permission

SCOPE = WorkflowScope(tenant_id="tenant-a", factory_id="factory-1")


class Request(BaseModel):
    value: int = 0


class Response(BaseModel):
    values: list[int]


def _capability(
    handler: Callable[[Request, TaskContext], Response],
    *,
    capability_id: str = "example",
    required_permissions: frozenset[Permission] = frozenset(),
) -> Capability:
    return Capability.register(
        capability_id,
        "1",
        input_model=Request,
        output_model=Response,
        handler=handler,
        required_permissions=required_permissions,
    )


def _task(
    task_id: str, *dependencies: str, capability_id: str = "example", version: str = "1"
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        capability_id=capability_id,
        capability_version=version,
        dependencies=dependencies,
    )


def _workflow(*tasks: TaskSpec) -> WorkflowSpec:
    return WorkflowSpec(workflow_id="advisory", version="1", tasks=tasks)


def test_deterministic_graph_and_scoped_immutable_dependency_outputs() -> None:
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        assert context.scope == SCOPE
        if context.task_id == "join":
            assert tuple(artifact.ref.task_id for artifact in context.dependencies) == ("a", "b")
            assert all(artifact.ref.scope == SCOPE for artifact in context.dependencies)
            assert context.dependency("a").read() == {"values": [1]}
            local = context.dependency("a").read()
            local["values"] = [99]
            assert context.dependency("a").read() == {"values": [1]}
            with pytest.raises(KeyError):
                context.dependency("upstream")
            return Response(values=[1, 2])
        return Response(values=[request.value])

    supervisor = Supervisor(CapabilityRegistry([_capability(handler)]))
    tasks = (_task("join", "b", "a"), _task("b"), _task("a", "upstream"), _task("upstream"))
    inputs = {"a": {"value": 1}, "b": {"value": 2}}
    result = supervisor.run(_workflow(*tasks), scope=SCOPE, inputs=inputs)
    second = supervisor.run(_workflow(*reversed(tasks)), scope=SCOPE, inputs=inputs)

    assert result == second
    assert calls == ["b", "upstream", "a", "join"] * 2
    artifact = result.tasks[-1].artifact
    assert artifact is not None
    assert artifact.read() == {"values": [1, 2]}
    assert artifact.ref.workflow_id == "advisory"
    assert artifact.ref.workflow_version == "1"
    assert artifact.ref.payload_sha256 == hashlib.sha256(artifact.payload_json.encode()).hexdigest()


@pytest.mark.parametrize(
    "invalid",
    [
        (_task("duplicate"), _task("duplicate")),
        (_task("repeat", "good", "good"),),
        (_task("unknown", "missing"),),
        (_task("cycle-a", "cycle-b"), _task("cycle-b", "cycle-a")),
        (_task("self", "self"),),
        (_task("unregistered", capability_id="never-registered"),),
        (_task("wrong-version", version="2"),),
    ],
)
def test_invalid_graph_executes_no_handlers(invalid: tuple[TaskSpec, ...]) -> None:
    calls: list[str] = []

    def handler(_request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        return Response(values=[])

    supervisor = Supervisor(CapabilityRegistry([_capability(handler)]))
    with pytest.raises(WorkflowValidationError):
        supervisor.run(_workflow(_task("good"), *invalid), scope=SCOPE)
    assert calls == []


def test_permission_failure_blocks_descendants_and_runs_independent_tasks() -> None:
    calls: list[str] = []

    def handler(_request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        return Response(values=[])

    registry = CapabilityRegistry(
        [
            _capability(handler),
            _capability(
                handler,
                capability_id="read-asset",
                required_permissions=frozenset({Permission.ASSET_READ}),
            ),
        ]
    )
    workflow = _workflow(
        _task("protected", capability_id="read-asset"),
        _task("waiting", "protected"),
        _task("descendant", "waiting"),
        _task("independent"),
    )
    supervisor = Supervisor(registry)
    result = supervisor.run(workflow, scope=SCOPE)
    outcomes = {task.task_id: task for task in result.tasks}

    assert calls == ["independent"]
    assert outcomes["protected"].failure == TaskFailure.PERMISSION_DENIED
    assert outcomes["waiting"].status == TaskStatus.BLOCKED
    assert outcomes["waiting"].blocked_by == ("protected",)
    assert outcomes["descendant"].blocked_by == ("waiting",)
    assert outcomes["independent"].status == TaskStatus.SUCCEEDED
    assert all(
        task.status == TaskStatus.SUCCEEDED
        for task in supervisor.run(
            workflow, scope=SCOPE, permissions=frozenset({Permission.ASSET_READ})
        ).tasks
    )


def test_invalid_input_output_and_handler_failure_have_stable_codes() -> None:
    calls: list[str] = []

    def handler(request: Request, context: TaskContext) -> Response:
        calls.append(context.task_id)
        if context.task_id == "invalid-output":
            return Response.model_construct(values=["wrong"])
        if context.task_id == "handler-error":
            raise RuntimeError("private details")
        return Response(values=[request.value])

    tasks = (
        _task("invalid-input"),
        _task("extra-input"),
        _task("oversize-input"),
        _task("invalid-output"),
        _task("handler-error"),
        _task("good"),
    )
    result = Supervisor(CapabilityRegistry([_capability(handler)])).run(
        _workflow(*tasks),
        scope=SCOPE,
        inputs={
            "invalid-input": {"value": "1"},
            "extra-input": {"unknown": True},
            "oversize-input": {"value": "a" * MAX_PAYLOAD_BYTES},
        },
    )

    assert calls == ["good", "handler-error", "invalid-output"]
    assert {task.task_id: task.failure for task in result.tasks} == {
        "invalid-input": TaskFailure.INPUT_INVALID,
        "extra-input": TaskFailure.INPUT_INVALID,
        "oversize-input": TaskFailure.INPUT_INVALID,
        "invalid-output": TaskFailure.OUTPUT_INVALID,
        "handler-error": TaskFailure.HANDLER_FAILED,
        "good": None,
    }
    assert "private details" not in result.model_dump_json()


def test_invoker_receives_validated_input_and_fresh_copy_for_each_attempt() -> None:
    observations: list[tuple[str, str]] = []

    def handler(request: Request, _context: TaskContext) -> Response:
        value = request.value
        request.value = -1
        return Response(values=[value])

    def invoke(
        capability: Capability, context: TaskContext, operation: Callable[[], BaseModel]
    ) -> BaseModel:
        observations.append((capability.capability_id, context.task_id))
        assert operation() == Response(values=[5])
        return operation()

    result = Supervisor(CapabilityRegistry([_capability(handler)]), invoker=invoke).run(
        _workflow(_task("sample")),
        scope=SCOPE,
        inputs={"sample": {"value": 5}},
    )

    assert observations == [("example", "sample")]
    assert result.tasks[0].artifact is not None
    assert result.tasks[0].artifact.read() == {"values": [5]}


def test_bounds_registry_conflicts_and_non_read_permissions_are_rejected() -> None:
    def handler(_request: Request, _context: TaskContext) -> Response:
        return Response(values=[])

    with pytest.raises(ValidationError):
        _workflow(*(_task(f"task-{i}") for i in range(MAX_TASKS + 1)))
    with pytest.raises(ValueError, match="only advisory read permissions"):
        _capability(handler, required_permissions=frozenset({Permission.DECISION_CREATE}))
    capability = _capability(handler)
    with pytest.raises(ValueError, match="Duplicate capability registration"):
        CapabilityRegistry([capability, capability])
    with pytest.raises(WorkflowValidationError, match="Input supplied for an unknown task"):
        Supervisor(CapabilityRegistry([capability])).run(
            _workflow(_task("known")), scope=SCOPE, inputs={"unknown": {}}
        )


@pytest.mark.parametrize("payload", ['{"n":1,"n":2}', "[]", '{"n": 1}', '{"n":2}'])
def test_artifact_rejects_malformed_noncanonical_or_mismatched_payload(payload: str) -> None:
    first_payload = '{"n":1}'
    ref = ArtifactRef(
        scope=SCOPE,
        workflow_id="advisory",
        workflow_version="1",
        task_id="sample",
        payload_sha256=hashlib.sha256(first_payload.encode()).hexdigest(),
    )
    assert TaskArtifact(ref=ref, payload_json=first_payload).read() == {"n": 1}
    with pytest.raises(ValidationError):
        TaskArtifact(ref=ref, payload_json=payload)
