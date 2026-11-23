"""Deterministic decision brief (machine-readable; every number comes from trusted records)."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.comparison import ComparisonResult
from qorvexa.industrial.decision_intelligence.models import Admitted, F, Rejection
from qorvexa.industrial.decision_intelligence.numeric import fmt
from qorvexa.industrial.decision_intelligence.pareto import ParetoResult
from qorvexa.industrial.decision_intelligence.ranking import RankingResult
from qorvexa.industrial.decision_intelligence.recommendation import Recommendation
from qorvexa.industrial.decision_intelligence.tradeoffs import TradeOff

BRIEF_SCHEMA_VERSION = "qorvexa-decision-brief/1.2"


class EvidenceRef(BaseModel):
    model_config = F
    scenario_id: str
    input_sha256: str
    spec_sha256: str
    model_sha256: str
    lineage: tuple[str, ...]
    report_status: str
    optimality_tier: str
    verification_levels: tuple[str, ...]
    unit_status: str = "LEGACY_UNITS_UNVERIFIED"
    unit_report_sha256: str = ""
    admission_status: str = "NOT_ADMITTED"
    admission_sha256: str = ""


class ObservedBehavior(BaseModel):
    model_config = F
    scenario_id: str
    changed_inputs: tuple[str, ...]
    classification: str  # always OBSERVED_SCENARIO_BEHAVIOR
    feasibility: str
    objective_absolute_change: Decimal | None
    mathematical_sensitivity: str = (
        "UNAVAILABLE: runtime results carry no verified dual information"
    )


class DecisionBrief(BaseModel):
    model_config = F
    schema_version: str
    brief_sha256: str
    executive_summary: tuple[str, ...]
    baseline_id: str
    objective_mode: str | None
    currency: str | None
    baseline_status: str
    comparison: ComparisonResult | None
    rejected_scenarios: tuple[Rejection, ...]
    trade_offs: dict[str, tuple[TradeOff, ...]]
    unavailable_analyses: tuple[str, ...]
    pareto: ParetoResult
    ranking: RankingResult | None
    objective_first_ranking: RankingResult | None
    observed_sensitivity: tuple[ObservedBehavior, ...]
    recommendation: Recommendation
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


UNAVAILABLE = (
    "Resource opportunity cost / shadow price: UNAVAILABLE (no verified dual or economics).",
    "Probabilistic risk or confidence: UNSUPPORTED (no validated uncertainty model).",
    "Market demand response and cost of scenario changes: UNAVAILABLE (not modeled).",
)
ASSUMPTIONS = (
    "Inputs are the user's declared values for a continuous LP; integer decisions, setups and "
    "scheduling are out of scope.",
    "Scenarios were compiled and solved independently; no result is reused across model hashes.",
)
LIMITATIONS = (
    "GAP_NUMERICAL is a numerical verification, not a formal proof of optimality.",
    "Comparisons describe observed behavior of discrete scenarios, not formal sensitivity.",
)


def evidence_ref(a: Admitted) -> EvidenceRef:
    r = a.run.result
    return EvidenceRef(
        scenario_id=a.run.scenario_id,
        input_sha256=a.input_sha256,
        spec_sha256=a.run.formulation.manifest.spec_sha256,
        model_sha256=a.model_sha256,
        lineage=a.run.formulation.manifest.lineage,
        report_status=a.report.status.value,
        unit_status=a.units_status,
        unit_report_sha256=a.unit_report_sha256,
        admission_status=a.admission_status,
        admission_sha256=a.admission_sha256,
        optimality_tier="" if r is None else r.optimality_tier.value,
        verification_levels=()
        if r is None
        else tuple(
            sorted(f"{c.claim_kind.value}:{c.level.value}:{c.status.value}" for c in r.claims)
        ),
    )


def observed(comparison: ComparisonResult) -> tuple[ObservedBehavior, ...]:
    out: list[ObservedBehavior] = []
    for c in comparison.compared:
        feas = (
            "FEASIBLE_VERIFIED"
            if c.objective is not None
            else "INFEASIBLE_VERIFIED"
            if c.status == "INFEASIBLE_VERIFIED"
            else c.status
        )
        out.append(
            ObservedBehavior(
                scenario_id=c.scenario_id,
                changed_inputs=tuple(
                    f"{x.kind}:{x.target_id}:{fmt(x.old)}->{fmt(x.new)}"
                    for x in c.input_diff.changes
                ),
                classification="OBSERVED_SCENARIO_BEHAVIOR",
                feasibility=feas,
                objective_absolute_change=None
                if c.objective is None
                else c.objective.absolute_change,
            )
        )
    return tuple(out)


def summary_lines(
    baseline: Admitted | Rejection,
    comparison: ComparisonResult | None,
    rec: Recommendation,
) -> tuple[str, ...]:
    lines: list[str] = [f"Decision objective: {rec.decision_objective}."]
    if isinstance(baseline, Rejection):
        lines.append(f"Baseline could not be admitted ({baseline.code}): {baseline.reason}.")
    elif baseline.solved:
        lines.append(
            f"Baseline objective: {fmt(baseline.metrics['objective'])} "
            f"{baseline.run.problem.currency} (numerically verified, GAP_NUMERICAL)."
        )
    else:
        lines.append(f"Baseline status: {baseline.report.status.value}.")
    if comparison is not None:
        for c in comparison.compared:
            if c.objective is not None:
                o = c.objective
                rel = "n/a" if o.relative_change is None else f"{fmt(o.relative_change * 100, 2)}%"
                lines.append(
                    f"{c.scenario_id}: objective {fmt(o.scenario)} ({fmt(o.absolute_change)}, "
                    f"{rel}) - {o.effect.value.lower()}."
                )
            else:
                lines.append(f"{c.scenario_id}: {c.status}.")
        for r in comparison.excluded:
            lines.append(f"{r.scenario_id}: excluded ({r.code}).")
    who = rec.recommended_scenario_id or "none"
    lines.append(f"Recommendation: {rec.status.value} (scenario: {who}).")
    lines.append("Human approval is required before any production change.")
    return tuple(lines)


def seal(**brief_fields: Any) -> DecisionBrief:
    """Compute the content hash over the canonical JSON of everything but the hash itself."""
    draft = DecisionBrief(brief_sha256="0" * 64, **brief_fields)
    body = draft.model_dump_json(exclude={"brief_sha256"})
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return draft.model_copy(update={"brief_sha256": digest})


def brief_json(brief: DecisionBrief) -> str:
    return brief.model_dump_json(indent=2)


__all__ = [
    "ASSUMPTIONS",
    "BRIEF_SCHEMA_VERSION",
    "LIMITATIONS",
    "UNAVAILABLE",
    "DecisionBrief",
    "EvidenceRef",
    "ObservedBehavior",
    "brief_json",
    "evidence_ref",
    "observed",
    "seal",
    "summary_lines",
]
