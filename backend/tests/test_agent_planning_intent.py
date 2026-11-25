"""Untrusted planning proposals can only prepare application-owned workflow inputs."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from qorvexa.agents import CapabilityRegistry, WorkflowScope
from qorvexa.agents.planning_adapter import (
    VERIFIED_PLANNING_ROUTE,
    PlanningRequest,
    verified_planning_capability,
)
from qorvexa.agents.planning_intent import (
    MAX_PROPOSAL_BYTES,
    IntentClarification,
    IntentClarificationReason,
    prepare_planning_intent,
)
from qorvexa.agents.routing import PreparedWorkflow, WorkflowRoute, WorkflowRouter
from qorvexa.auth.principal import Permission
from qorvexa.industrial.co_orchestration import CoOrchestrator
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.highs_adapter import HighsAdapter

SCOPE = WorkflowScope(tenant_id="tenant-a", factory_id="factory-a")
PERMISSIONS = frozenset({Permission.ASSET_READ})
SELECTION_KEY = "reviewed-shift"
BINDING = PlanningRequest(case_id="private-case", case_sha256="a" * 64)


def _candidate(**changes: object) -> dict[str, object]:
    return {
        "workflow_id": VERIFIED_PLANNING_ROUTE.workflow.workflow_id,
        "workflow_version": VERIFIED_PLANNING_ROUTE.workflow.version,
        "arguments": {"selection_key": SELECTION_KEY},
        **changes,
    }


def _proposal(*candidates: dict[str, object]) -> str:
    return json.dumps({"candidates": candidates})


def _router(
    routes: tuple[WorkflowRoute, ...] = (VERIFIED_PLANNING_ROUTE,),
) -> tuple[WorkflowRouter, Mock]:
    resolve = Mock()
    specialist = CoOrchestrator(LPOrchestrator(HighsAdapter()))
    registry = CapabilityRegistry((verified_planning_capability(specialist, resolve),))
    return WorkflowRouter(registry, routes), resolve


def test_selection_uses_only_the_trusted_binding_and_does_not_execute() -> None:
    router, resolve = _router()
    case_bindings = {(SCOPE, SELECTION_KEY): BINDING}
    wire = _proposal(_candidate()).ljust(MAX_PROPOSAL_BYTES).encode("utf-8")

    prepared = prepare_planning_intent(
        router, wire, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
    )

    assert isinstance(prepared, PreparedWorkflow)
    assert prepared.workflow == VERIFIED_PLANNING_ROUTE.workflow
    assert prepared.intake_task_id == VERIFIED_PLANNING_ROUTE.intake_task_id
    assert prepared.scope == SCOPE and prepared.permissions == PERMISSIONS
    trusted_arguments = {"case_id": "private-case", "case_sha256": "a" * 64}
    assert prepared.inputs() == {prepared.intake_task_id: trusted_arguments}
    case_bindings[(SCOPE, SELECTION_KEY)] = PlanningRequest(
        case_id="changed-case", case_sha256="b" * 64
    )
    prepared.inputs()[prepared.intake_task_id]["case_id"] = "changed-after-return"
    assert prepared.inputs() == {prepared.intake_task_id: trusted_arguments}
    resolve.assert_not_called()


@pytest.mark.parametrize(
    ("wire", "reason"),
    [
        (None, IntentClarificationReason.MALFORMED_PROPOSAL),
        ({"candidates": []}, IntentClarificationReason.MALFORMED_PROPOSAL),
        (_proposal(), IntentClarificationReason.MISSING_PROPOSAL),
        (_proposal(_candidate(), _candidate()), IntentClarificationReason.AMBIGUOUS_PROPOSAL),
        (
            _proposal(_candidate(workflow_id="approve-decision")),
            IntentClarificationReason.UNSUPPORTED_ROUTE,
        ),
        (_proposal(_candidate(workflow_version="0")), IntentClarificationReason.UNSUPPORTED_ROUTE),
        (_proposal(_candidate(workflow_version=1)), IntentClarificationReason.MALFORMED_PROPOSAL),
        (
            _proposal(_candidate(arguments={"selection_key": SELECTION_KEY, "case_id": "other"})),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (
            _proposal(
                _candidate(arguments={"selection_key": SELECTION_KEY, "case_sha256": "a" * 64})
            ),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (
            _proposal(_candidate(scope={"factory_id": "other"})),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (
            _proposal(_candidate(permissions=["asset:read"])),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (
            _proposal(_candidate(workflow={"tasks": [{"task_id": "approve"}]})),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        ('{"candidates":[],"candidates":[]}', IntentClarificationReason.MALFORMED_PROPOSAL),
        (
            _proposal(_candidate()).replace(
                '"selection_key": "reviewed-shift"',
                '"selection_key": "other", "selection_key": "reviewed-shift"',
            ),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (b"\xff{}", IntentClarificationReason.MALFORMED_PROPOSAL),
        (
            _proposal(_candidate()).ljust(MAX_PROPOSAL_BYTES + 1),
            IntentClarificationReason.MALFORMED_PROPOSAL,
        ),
        (_proposal(*([_candidate()] * 5)), IntentClarificationReason.MALFORMED_PROPOSAL),
    ],
)
def test_unusable_proposals_do_not_consult_bindings_or_resolve_a_case(
    wire: str | bytes,
    reason: IntentClarificationReason,
) -> None:
    router, resolve = _router()
    bindings = Mock(spec=dict)
    result = prepare_planning_intent(
        router, wire, scope=SCOPE, permissions=PERMISSIONS, case_bindings=bindings
    )

    assert result == IntentClarification(reason)
    bindings.get.assert_not_called()
    resolve.assert_not_called()


def test_case_selection_is_scoped_and_unavailable_contexts_do_not_leak() -> None:
    router, resolve = _router()
    case_bindings = {(SCOPE, SELECTION_KEY): BINDING}
    proposal = _proposal(_candidate())
    unavailable = IntentClarification(IntentClarificationReason.CONTEXT_UNAVAILABLE)

    assert (
        prepare_planning_intent(
            router, proposal, scope=SCOPE, permissions=frozenset(), case_bindings=case_bindings
        )
        == unavailable
    )
    for other_scope in (
        WorkflowScope(tenant_id="tenant-a", asset_id="factory-a"),
        WorkflowScope(tenant_id="other-tenant", factory_id="factory-a"),
        WorkflowScope(tenant_id="tenant-a", factory_id="other-factory"),
    ):
        assert (
            prepare_planning_intent(
                router,
                proposal,
                scope=other_scope,
                permissions=PERMISSIONS,
                case_bindings=case_bindings,
            )
            == unavailable
        )
    unknown = _proposal(_candidate(arguments={"selection_key": "unavailable"}))
    assert (
        prepare_planning_intent(
            router, unknown, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
        )
        == unavailable
    )
    resolve.assert_not_called()


def test_registry_still_validates_the_trusted_binding() -> None:
    router, resolve = _router()
    invalid = PlanningRequest.model_construct(case_id="private-case", case_sha256="invalid")

    result = prepare_planning_intent(
        router,
        _proposal(_candidate()),
        scope=SCOPE,
        permissions=PERMISSIONS,
        case_bindings={(SCOPE, SELECTION_KEY): invalid},
    )

    assert result == IntentClarification(IntentClarificationReason.CONTEXT_UNAVAILABLE)
    resolve.assert_not_called()


@pytest.mark.parametrize("different_graph", [False, True])
def test_router_must_be_configured_with_the_exact_approved_template(different_graph: bool) -> None:
    route = VERIFIED_PLANNING_ROUTE
    if different_graph:
        (task,) = route.workflow.tasks
        alternate_task = task.model_copy(update={"task_id": "different-intake"})
        route = replace(
            route,
            workflow=route.workflow.model_copy(update={"tasks": (alternate_task,)}),
            intake_task_id=alternate_task.task_id,
        )
        routes = (route,)
    else:
        routes = ()
    router, resolve = _router(routes)

    result = prepare_planning_intent(
        router,
        _proposal(_candidate()),
        scope=SCOPE,
        permissions=PERMISSIONS,
        case_bindings={(SCOPE, SELECTION_KEY): BINDING},
    )

    assert result == IntentClarification(IntentClarificationReason.UNSUPPORTED_ROUTE)
    resolve.assert_not_called()
