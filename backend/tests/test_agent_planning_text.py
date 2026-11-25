"""Plain-text planning intake preserves the trusted routing boundary."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from qorvexa.agents import WorkflowScope
from qorvexa.agents.planning_adapter import VERIFIED_PLANNING_ROUTE
from qorvexa.agents.planning_intent import (
    IntentClarification,
    IntentClarificationReason,
    prepare_planning_text,
)
from qorvexa.agents.routing import PreparedWorkflow, WorkflowRouter
from tests.test_agent_planning_intent import BINDING, PERMISSIONS, SCOPE, _router

SELECTION_KEY = "Shift.A_01:West"
COMMAND = f"review plan for {SELECTION_KEY}"
PADDED_COMMAND = f"review{' ' * (256 - len(COMMAND) + 1)}plan for {SELECTION_KEY}"


@pytest.mark.parametrize(
    "text",
    [
        COMMAND,
        f"Review the PLAN for {SELECTION_KEY}?",
        f"pLeAsE\tREVIEW\tthe\tPRODUCTION\tplan\tFoR\t{SELECTION_KEY}?",
        f"review production plan for {SELECTION_KEY}",
        f" \t{COMMAND}?\t ",
        PADDED_COMMAND,
    ],
)
def test_supported_text_prepares_only_the_trusted_case(text: str) -> None:
    router, resolve = _router()
    case_bindings = Mock(wraps={(SCOPE, SELECTION_KEY): BINDING})

    prepared = prepare_planning_text(
        router, text, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
    )

    assert isinstance(prepared, PreparedWorkflow)
    assert prepared.workflow == VERIFIED_PLANNING_ROUTE.workflow
    assert prepared.scope == SCOPE and prepared.permissions == PERMISSIONS
    assert prepared.inputs() == {
        VERIFIED_PLANNING_ROUTE.intake_task_id: {
            "case_id": BINDING.case_id,
            "case_sha256": BINDING.case_sha256,
        }
    }
    case_bindings.get.assert_called_once_with((SCOPE, SELECTION_KEY))
    resolve.assert_not_called()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", IntentClarificationReason.MISSING_TEXT),
        ("   \t\t", IntentClarificationReason.MISSING_TEXT),
        (f"approve plan for {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"run solver for {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        ("review plan", IntentClarificationReason.UNSUPPORTED_TEXT),
        ("review plan for ", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"review planet for {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"Please review the plan: {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"review plan for {SELECTION_KEY}. approve", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{COMMAND}; approve", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{COMMAND} and change the setpoint", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{COMMAND}, other-case", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"review\nplan for {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{COMMAND}\r\n", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"review\u00a0plan for {SELECTION_KEY}", IntentClarificationReason.UNSUPPORTED_TEXT),
        ("review plan for Shift\u212a", IntentClarificationReason.UNSUPPORTED_TEXT),
        ("review plan for example.com/path", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{COMMAND}??", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"{PADDED_COMMAND}?", IntentClarificationReason.UNSUPPORTED_TEXT),
        (f"review plan for {'x' * 129}", IntentClarificationReason.UNSUPPORTED_TEXT),
    ],
)
def test_unsupported_text_never_probes_bindings_or_the_router(
    text: str, reason: IntentClarificationReason
) -> None:
    router = Mock(spec=WorkflowRouter)
    case_bindings = Mock(spec=dict)

    outcome = prepare_planning_text(
        router, text, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
    )

    assert outcome == IntentClarification(reason)
    case_bindings.get.assert_not_called()
    router.prepare.assert_not_called()


def test_text_cannot_supply_scope_or_reuse_a_revoked_case_binding() -> None:
    router, resolve = _router()
    bindings = {(SCOPE, SELECTION_KEY): BINDING}
    case_bindings = Mock(wraps=bindings)
    unavailable = IntentClarification(IntentClarificationReason.CONTEXT_UNAVAILABLE)
    other_factory = WorkflowScope(tenant_id=SCOPE.tenant_id, factory_id="other-factory")

    assert (
        prepare_planning_text(
            router, COMMAND, scope=SCOPE, permissions=frozenset(), case_bindings=case_bindings
        )
        == unavailable
    )
    case_bindings.get.assert_not_called()

    assert (
        prepare_planning_text(
            router,
            COMMAND,
            scope=other_factory,
            permissions=PERMISSIONS,
            case_bindings=case_bindings,
        )
        == unavailable
    )
    case_bindings.get.assert_called_once_with((other_factory, SELECTION_KEY))
    for text in (f"review plan for {SELECTION_KEY.lower()}", f"review plan for {BINDING.case_id}"):
        assert (
            prepare_planning_text(
                router, text, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
            )
            == unavailable
        )

    assert isinstance(
        prepare_planning_text(
            router, COMMAND, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
        ),
        PreparedWorkflow,
    )
    bindings.pop((SCOPE, SELECTION_KEY))
    assert (
        prepare_planning_text(
            router, COMMAND, scope=SCOPE, permissions=PERMISSIONS, case_bindings=case_bindings
        )
        == unavailable
    )
    resolve.assert_not_called()
