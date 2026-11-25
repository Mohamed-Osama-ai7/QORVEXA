"""Admit an upstream planning proposal using only application-owned case bindings."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import Field

from qorvexa.agents.contracts import FrozenContract, Identifier, WorkflowScope
from qorvexa.agents.planning_adapter import VERIFIED_PLANNING_ROUTE, PlanningRequest
from qorvexa.agents.routing import (
    PreparedWorkflow,
    RoutingFailure,
    WorkflowRouter,
    WorkflowRoutingError,
)
from qorvexa.auth.principal import Permission

MAX_PROPOSAL_BYTES: Final = 4_096
MAX_CANDIDATES: Final = 4
MAX_PLANNING_TEXT_LENGTH: Final = 256
_PLANNING_REQUEST: Final = re.compile(
    r"(?:please[ \t]+)?review[ \t]+(?:the[ \t]+)?(?:production[ \t]+)?plan[ \t]+for[ \t]+"
    r"(?P<selection>[a-z0-9][a-z0-9._:-]{0,127})\??",
    re.IGNORECASE | re.ASCII,
)


class IntentClarificationReason(StrEnum):
    MALFORMED_PROPOSAL = "malformed_proposal"
    MISSING_PROPOSAL = "missing_proposal"
    AMBIGUOUS_PROPOSAL = "ambiguous_proposal"
    UNSUPPORTED_ROUTE = "unsupported_route"
    CONTEXT_UNAVAILABLE = "context_unavailable"
    MISSING_TEXT = "missing_text"
    UNSUPPORTED_TEXT = "unsupported_text"


@dataclass(frozen=True, slots=True)
class IntentClarification:
    """Safe refusal with no prepared task or echoed proposal content."""

    reason: IntentClarificationReason


class _PlanningArguments(FrozenContract):
    selection_key: Identifier


class _RouteCandidate(FrozenContract):
    workflow_id: Identifier
    workflow_version: Identifier
    arguments: _PlanningArguments


class _Proposal(FrozenContract):
    candidates: list[_RouteCandidate] = Field(max_length=MAX_CANDIDATES)


def _unique_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    obj = dict(items)
    if len(obj) != len(items):
        raise ValueError("Duplicate JSON member")
    return obj


def _parse(proposal: str | bytes) -> _Proposal:
    if not isinstance(proposal, (str, bytes)):
        raise ValueError("Proposal must be UTF-8 JSON text")
    if len(proposal) > MAX_PROPOSAL_BYTES:
        raise ValueError("Proposal exceeds the byte limit")
    wire = proposal.encode("utf-8") if isinstance(proposal, str) else proposal
    if len(wire) > MAX_PROPOSAL_BYTES:
        raise ValueError("Proposal exceeds the byte limit")
    decoded = json.loads(wire.decode("utf-8"), object_pairs_hook=_unique_keys)
    return _Proposal.model_validate(decoded, strict=True, extra="forbid")


def prepare_planning_intent(
    router: WorkflowRouter,
    proposal_json: str | bytes,
    *,
    scope: WorkflowScope,
    permissions: frozenset[Permission],
    case_bindings: Mapping[tuple[WorkflowScope, str], PlanningRequest],
) -> PreparedWorkflow | IntentClarification:
    """Prepare one approved planning route from an untrusted proposal and trusted context."""
    try:
        proposal = _parse(proposal_json)
    except (ValueError, RecursionError):
        return IntentClarification(IntentClarificationReason.MALFORMED_PROPOSAL)
    if not proposal.candidates:
        return IntentClarification(IntentClarificationReason.MISSING_PROPOSAL)
    if len(proposal.candidates) != 1:
        return IntentClarification(IntentClarificationReason.AMBIGUOUS_PROPOSAL)

    candidate = proposal.candidates[0]
    route = VERIFIED_PLANNING_ROUTE
    if (
        candidate.workflow_id != route.workflow.workflow_id
        or candidate.workflow_version != route.workflow.version
    ):
        return IntentClarification(IntentClarificationReason.UNSUPPORTED_ROUTE)

    return _prepare_bound_case(
        router, candidate.arguments.selection_key, scope, permissions, case_bindings
    )


def prepare_planning_text(
    router: WorkflowRouter,
    text: str,
    *,
    scope: WorkflowScope,
    permissions: frozenset[Permission],
    case_bindings: Mapping[tuple[WorkflowScope, str], PlanningRequest],
) -> PreparedWorkflow | IntentClarification:
    """Recognize an explicit request to review exactly one named production plan."""
    if not isinstance(text, str) or len(text) > MAX_PLANNING_TEXT_LENGTH or not text.isascii():
        return IntentClarification(IntentClarificationReason.UNSUPPORTED_TEXT)
    request = text.strip(" \t")
    if not request:
        return IntentClarification(IntentClarificationReason.MISSING_TEXT)
    match = _PLANNING_REQUEST.fullmatch(request)
    if match is None:
        return IntentClarification(IntentClarificationReason.UNSUPPORTED_TEXT)
    return _prepare_bound_case(router, match["selection"], scope, permissions, case_bindings)


def _prepare_bound_case(
    router: WorkflowRouter,
    selection_key: str,
    scope: WorkflowScope,
    permissions: frozenset[Permission],
    case_bindings: Mapping[tuple[WorkflowScope, str], PlanningRequest],
) -> PreparedWorkflow | IntentClarification:
    route = VERIFIED_PLANNING_ROUTE
    unavailable = IntentClarification(IntentClarificationReason.CONTEXT_UNAVAILABLE)
    if scope.factory_id is None or Permission.ASSET_READ not in permissions:
        return unavailable
    binding = case_bindings.get((scope, selection_key))
    if binding is None:
        return unavailable

    try:
        prepared = router.prepare(
            route.workflow.workflow_id,
            scope=scope,
            permissions=permissions,
            arguments={"case_id": binding.case_id, "case_sha256": binding.case_sha256},
        )
    except WorkflowRoutingError as exc:
        if exc.failure is RoutingFailure.UNKNOWN_ROUTE:
            return IntentClarification(IntentClarificationReason.UNSUPPORTED_ROUTE)
        return unavailable
    if prepared.workflow != route.workflow or prepared.intake_task_id != route.intake_task_id:
        return IntentClarification(IntentClarificationReason.UNSUPPORTED_ROUTE)
    return prepared
