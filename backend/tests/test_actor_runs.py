"""Actor-owned workflow authorization and durable key isolation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Final

import pytest
from pydantic import BaseModel, field_validator

from qorvexa.agents import (
    Capability,
    CapabilityRegistry,
    ExecutionPolicy,
    ExecutionService,
    RunConflictError,
    RunEventKind,
    RunState,
    RunStore,
    TaskContext,
    TaskSpec,
    WorkflowScope,
    WorkflowSpec,
)
from qorvexa.agents.actor_runs import ActorRunService, ActorRunUnavailableError, ScopeGrant
from qorvexa.agents.routing import RouteScope, WorkflowRoute, WorkflowRouter
from qorvexa.auth.principal import Permission, Principal, Role

ASSET: Final = WorkflowScope(tenant_id="tenant", asset_id="asset")
FACTORY: Final = WorkflowScope(tenant_id="tenant", factory_id="asset")
OWNER: Final = Principal("actor|one", "tenant", frozenset({Role.VIEWER}), frozenset({"asset"}))
PEER: Final = Principal("actor|two", "tenant", frozenset({Role.VIEWER}), frozenset({"asset"}))
ASSET_READ: Final = frozenset({Permission.ASSET_READ})


class Message(BaseModel):
    content: str


def _route(workflow_id: str, scope: RouteScope) -> WorkflowRoute:
    return WorkflowRoute(
        WorkflowSpec(
            workflow_id=workflow_id,
            version="1",
            tasks=(TaskSpec(task_id="intake", capability_id="evidence", capability_version="1"),),
        ),
        scope,
        "intake",
    )


def _service(
    path: Path,
    grant: ScopeGrant,
    handler: Callable[[Message, TaskContext], Message],
    *,
    wall_clock: Callable[[], float] = lambda: 100.0,
    input_model: type[Message] = Message,
) -> tuple[ActorRunService, ExecutionService, WorkflowSpec]:
    capability = Capability.register(
        "evidence",
        "1",
        input_model=input_model,
        output_model=Message,
        handler=handler,
        required_permissions=ASSET_READ,
    )
    registry = CapabilityRegistry([capability])
    route = _route("advisory", RouteScope.ASSET)
    router = WorkflowRouter(
        registry,
        [
            route,
            _route("other-advisory", RouteScope.ASSET),
            _route("factory-advisory", RouteScope.FACTORY),
        ],
    )
    execution = ExecutionService(registry, RunStore(path), wall_clock=wall_clock)
    return (
        ActorRunService(router, execution, grant, policy=ExecutionPolicy(run_timeout_s=15)),
        execution,
        route.workflow,
    )


def _echo(message: Message, _context: TaskContext) -> Message:
    return message


def test_actors_cannot_access_or_recover_peers_runs_with_the_same_key(tmp_path: Path) -> None:
    actor_calls: list[tuple[str, WorkflowScope]] = []

    def grant(principal: Principal, scope: WorkflowScope) -> frozenset[Permission]:
        actor_calls.append((principal.actor_id, scope))
        return ASSET_READ

    service, _, _ = _service(tmp_path / "actor.sqlite", grant, _echo)
    args = {"scope": ASSET, "workflow_id": "advisory", "client_key": "client-key"}

    owner = service.submit(OWNER, **args, arguments={"content": "owner evidence"})
    assert service.read(OWNER, **args) == owner
    assert service.events(OWNER, **args)[-1].kind is RunEventKind.COMPLETED
    assert service.recover_expired(OWNER, **args) == owner
    assert actor_calls == [(OWNER.actor_id, ASSET)] * 6
    for lookup in (service.read, service.events, service.recover_expired):
        with pytest.raises(ActorRunUnavailableError, match=r"^Workflow run unavailable$"):
            lookup(PEER, **args)
    peer = service.submit(PEER, **args, arguments={"content": "peer evidence"})
    assert peer.run_id != owner.run_id
    assert service.read(PEER, **args) == peer
    assert service.read(OWNER, **args) == owner
    assert "owner evidence" not in repr(service.events(PEER, **args))


def test_one_client_key_per_actor_and_scope_even_across_routes(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path / "route.sqlite", lambda *_: ASSET_READ, _echo)
    first = service.submit(
        OWNER, scope=ASSET, workflow_id="advisory", client_key="same", arguments={"content": "one"}
    )
    with pytest.raises(RunConflictError):
        service.submit(
            OWNER,
            scope=ASSET,
            workflow_id="other-advisory",
            client_key="same",
            arguments={"content": "one"},
        )
    with pytest.raises(RunConflictError):
        service.submit(
            OWNER,
            scope=ASSET,
            workflow_id="advisory",
            client_key="same",
            arguments={"content": "changed"},
        )
    for lookup in (service.read, service.events, service.recover_expired):
        with pytest.raises(ActorRunUnavailableError, match=r"^Workflow run unavailable$"):
            lookup(OWNER, scope=ASSET, workflow_id="other-advisory", client_key="same")
    another_resource = service.submit(
        OWNER,
        scope=FACTORY,
        workflow_id="factory-advisory",
        client_key="same",
        arguments={"content": "one"},
    )
    assert another_resource.run_id != first.run_id


def test_exact_tenant_asset_and_factory_grants_are_required(tmp_path: Path) -> None:
    seen: set[WorkflowScope] = set()

    def grant(_principal: Principal, scope: WorkflowScope) -> frozenset[Permission] | None:
        seen.add(scope)
        return ASSET_READ if scope == ASSET else None

    service, _, _ = _service(tmp_path / "scopes.sqlite", grant, _echo)
    other_tenant = WorkflowScope(tenant_id="other", asset_id="asset")
    other_asset = WorkflowScope(tenant_id="tenant", asset_id="other")
    for scope in (other_tenant, other_asset):
        with pytest.raises(ActorRunUnavailableError):
            service.submit(
                OWNER,
                scope=scope,
                workflow_id="advisory",
                client_key="a",
                arguments={"content": "x"},
            )
    assert not seen
    with pytest.raises(ActorRunUnavailableError):
        service.submit(
            OWNER,
            scope=FACTORY,
            workflow_id="factory-advisory",
            client_key="a",
            arguments={"content": "x"},
        )
    assert seen == {FACTORY}


def test_current_grants_must_keep_required_permissions_and_ignore_unrelated_changes(
    tmp_path: Path,
) -> None:
    granted: frozenset[Permission] | None = ASSET_READ | {Permission.INCIDENT_READ}
    calls = 0

    def handle(message: Message, _context: TaskContext) -> Message:
        nonlocal calls
        calls += 1
        return message

    service, _, _ = _service(tmp_path / "required.sqlite", lambda *_: granted, handle)
    key = {"scope": ASSET, "workflow_id": "advisory", "client_key": "client-key"}
    arguments = {"content": "one"}
    original = service.submit(OWNER, **key, arguments=arguments)
    granted = ASSET_READ
    assert service.read(OWNER, **key) == original
    assert service.submit(OWNER, **key, arguments=arguments) == original
    granted = ASSET_READ | {Permission.ANOMALY_READ}
    assert service.submit(OWNER, **key, arguments=arguments) == original
    for denied in (frozenset({Permission.INCIDENT_READ}), None):
        granted = denied
        for lookup in (service.read, service.events, service.recover_expired):
            with pytest.raises(ActorRunUnavailableError):
                lookup(OWNER, **key)
        with pytest.raises(ActorRunUnavailableError):
            service.submit(OWNER, **key, arguments=arguments)
    assert calls == 1


def test_submit_rechecks_grant_before_releasing_result(tmp_path: Path) -> None:
    active = True

    def handle(message: Message, _context: TaskContext) -> Message:
        nonlocal active
        active = False
        return message

    service, _, _ = _service(
        tmp_path / "release.sqlite", lambda *_: ASSET_READ if active else None, handle
    )
    key = {"scope": ASSET, "workflow_id": "advisory", "client_key": "client-key"}
    with pytest.raises(ActorRunUnavailableError):
        service.submit(OWNER, **key, arguments={"content": "pinned until reauthorized"})
    with pytest.raises(ActorRunUnavailableError):
        service.read(OWNER, **key)
    active = True
    assert service.read(OWNER, **key).state is RunState.COMPLETED


def test_concurrent_actor_duplicate_reuses_the_live_claim(tmp_path: Path) -> None:
    entered = Event()
    release = Event()
    invocations: list[str] = []

    def handle(message: Message, _context: TaskContext) -> Message:
        invocations.append(message.content)
        entered.set()
        assert release.wait(timeout=10)
        return message

    service, _, _ = _service(tmp_path / "duplicate.sqlite", lambda *_: ASSET_READ, handle)
    params = {"scope": ASSET, "workflow_id": "advisory", "client_key": "same"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.submit, OWNER, **params, arguments={"content": "one"})
        try:
            assert entered.wait(timeout=10)
            duplicate = service.submit(OWNER, **params, arguments={"content": "one"})
            assert duplicate.state is RunState.RUNNING
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result.state is RunState.COMPLETED and result.run_id == duplicate.run_id
    assert invocations == ["one"]


def test_grant_revoked_during_intake_validation_prevents_execution(tmp_path: Path) -> None:
    active = True
    calls = 0

    class RevokingInput(Message):
        @field_validator("content")
        @classmethod
        def revoke(cls, value: str) -> str:
            nonlocal active
            active = False
            return value

    def handle(message: Message, _context: TaskContext) -> Message:
        nonlocal calls
        calls += 1
        return message

    service, _, _ = _service(
        tmp_path / "intake.sqlite",
        lambda *_: ASSET_READ if active else None,
        handle,
        input_model=RevokingInput,
    )
    args = {"scope": ASSET, "workflow_id": "advisory", "client_key": "intake"}
    with pytest.raises(ActorRunUnavailableError):
        service.submit(OWNER, **args, arguments={"content": "revoke"})
    active = True
    with pytest.raises(ActorRunUnavailableError):
        service.read(OWNER, **args)
    assert calls == 0


def test_recovery_authorizes_before_mutation_and_never_reinvokes(tmp_path: Path) -> None:
    class WorkerExit(BaseException):
        pass

    active = True
    now = 100.0
    invocations = 0

    def crash(_message: Message, _context: TaskContext) -> Message:
        nonlocal invocations
        invocations += 1
        raise WorkerExit

    service, _, _ = _service(
        tmp_path / "recovery.sqlite",
        lambda *_: ASSET_READ if active else None,
        crash,
        wall_clock=lambda: now,
    )
    key = {"scope": ASSET, "workflow_id": "advisory", "client_key": "crash"}
    with pytest.raises(WorkerExit):
        service.submit(OWNER, **key, arguments={"content": "original"})
    now = 120.0
    active = False
    with pytest.raises(ActorRunUnavailableError):
        service.recover_expired(OWNER, **key)
    active = True
    with pytest.raises(ActorRunUnavailableError):
        service.recover_expired(PEER, **key)
    assert service.read(OWNER, **key).state is RunState.RUNNING
    interrupted = service.recover_expired(OWNER, **key)
    assert interrupted.state is RunState.INTERRUPTED
    assert service.submit(OWNER, **key, arguments={"content": "original"}) == interrupted
    assert service.events(OWNER, **key)[-1].kind is RunEventKind.INTERRUPTED
    assert invocations == 1


def test_legacy_storage_key_cannot_collide_with_actor_partition(tmp_path: Path) -> None:
    service, execution, workflow = _service(
        tmp_path / "legacy.sqlite", lambda *_: ASSET_READ, _echo
    )
    actor_key = hashlib.sha256(
        json.dumps(
            ["qorvexa/actor-run/1", OWNER.actor_id, ASSET.model_dump(), "sample"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    legacy = execution.run(
        workflow,
        scope=ASSET,
        idempotency_key=actor_key,
        inputs={"intake": {"content": "legacy"}},
        permissions=ASSET_READ,
    )
    actor = service.submit(
        OWNER,
        scope=ASSET,
        workflow_id="advisory",
        client_key="sample",
        arguments={"content": "actor-owned"},
    )
    assert actor.run_id != legacy.run_id
    assert execution.get(ASSET, actor_key) == legacy
    assert service.read(OWNER, scope=ASSET, workflow_id="advisory", client_key="sample") == actor


@pytest.mark.parametrize(
    ("key", "route", "arguments"),
    [
        ("invalid key", "advisory", {"content": "x"}),
        ("x", "unknown", {"content": "x"}),
        ("x", "advisory", {"content": False}),
        ("x", "advisory", {"content": "x", "scope": "forged"}),
    ],
)
def test_malformed_and_unknown_intake_fail_uniformly(
    tmp_path: Path, key: str, route: str, arguments: dict[str, str | bool]
) -> None:
    service, _, _ = _service(tmp_path / "invalid.sqlite", lambda *_: ASSET_READ, _echo)
    with pytest.raises(ActorRunUnavailableError, match=r"^Workflow run unavailable$"):
        service.submit(OWNER, scope=ASSET, workflow_id=route, client_key=key, arguments=arguments)
