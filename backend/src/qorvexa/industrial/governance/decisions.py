"""Immutable, hash-chained human decision history bound to a sealed Decision Intelligence brief."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.decision_intelligence.explanation import DecisionBrief
from qorvexa.industrial.decision_intelligence.recommendation import RecommendationStatus
from qorvexa.industrial.governance.authz import Principal, Role

GENESIS = "0" * 64
_APPROVABLE = {RecommendationStatus.RECOMMENDED, RecommendationStatus.CONDITIONALLY_RECOMMENDED}


class DecisionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Verdict(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seq: int
    prev_sha256: str
    session_id: str
    brief_sha256: str
    formulation_record_sha256: str
    scenario_id: str | None
    verdict: Verdict
    approver_id: str
    self_approved: bool
    acknowledged_assumptions: tuple[str, ...]
    reason: str
    recorded_at: str
    record_sha256: str = ""


def record_digest(r: DecisionRecord) -> str:
    return hashlib.sha256(r.model_dump_json(exclude={"record_sha256"}).encode()).hexdigest()


def brief_digest_ok(brief: DecisionBrief) -> bool:
    body = brief.model_dump_json(exclude={"brief_sha256"})
    return hashlib.sha256(body.encode()).hexdigest() == brief.brief_sha256


@dataclass(frozen=True)
class DecisionHistory:
    records: tuple[DecisionRecord, ...] = ()

    @property
    def head(self) -> str:
        return self.records[-1].record_sha256 if self.records else GENESIS

    def verify(self) -> None:
        prev = GENESIS
        for i, r in enumerate(self.records, 1):
            if r.seq != i or r.prev_sha256 != prev or r.record_sha256 != record_digest(r):
                raise DecisionError("HISTORY_TAMPERED", f"decision record {i} is inconsistent")
            prev = r.record_sha256

    def record(
        self,
        *,
        brief: DecisionBrief,
        session_id: str,
        formulation_record_sha256: str,
        principal: Principal,
        verdict: Verdict,
        scenario_id: str | None,
        acknowledged: frozenset[str],
        reason: str,
        recorded_at: str,
        brief_created_by: str,
        allow_self_approval: bool,
    ) -> DecisionHistory:
        self.verify()
        principal.require(Role.APPROVER)
        if not brief_digest_ok(brief):
            raise DecisionError("BRIEF_TAMPERED", "brief content does not match its hash")
        if len(reason.strip()) < 10:
            raise DecisionError("REASON_REQUIRED", "a written reason of 10+ characters is required")
        self_approved = principal.id == brief_created_by
        if verdict is Verdict.APPROVED:
            rec = brief.recommendation
            if rec.status not in _APPROVABLE or rec.recommended_scenario_id is None:
                raise DecisionError("NOT_APPROVABLE", f"brief status is {rec.status.value}")
            if scenario_id != rec.recommended_scenario_id:
                raise DecisionError(
                    "WRONG_SCENARIO", "only the recommended scenario can be approved"
                )
            missing = set(rec.unresolved_assumptions) - acknowledged
            if missing:
                raise DecisionError(
                    "ASSUMPTIONS_NOT_ACKNOWLEDGED", f"acknowledge {sorted(missing)}"
                )
            if self_approved and not allow_self_approval:
                raise DecisionError(
                    "SELF_APPROVAL", "separation of duties: another approver needed"
                )
            if any(
                r.brief_sha256 == brief.brief_sha256 and r.verdict is Verdict.APPROVED
                for r in self.records
            ):
                raise DecisionError("ALREADY_DECIDED", "this brief was already approved")
        draft = DecisionRecord(
            seq=len(self.records) + 1,
            prev_sha256=self.head,
            session_id=session_id,
            brief_sha256=brief.brief_sha256,
            formulation_record_sha256=formulation_record_sha256,
            scenario_id=scenario_id,
            verdict=verdict,
            approver_id=principal.id,
            self_approved=self_approved,
            acknowledged_assumptions=tuple(sorted(acknowledged)),
            reason=reason.strip(),
            recorded_at=recorded_at,
        )
        sealed = draft.model_copy(update={"record_sha256": record_digest(draft)})
        return DecisionHistory((*self.records, sealed))
