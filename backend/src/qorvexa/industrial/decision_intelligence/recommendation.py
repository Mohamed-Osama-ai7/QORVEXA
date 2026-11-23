"""Recommendation policy. No probabilistic confidence: only explicit evidence coverage.

Policy (first match wins):
  1. baseline not verified-solved or no solved candidate        -> NO_VERIFIED_RECOMMENDATION
  2. ranking configuration missing/invalid or hard requirements
     met by nobody                                              -> REQUIRES_HUMAN_REVIEW
  3. several scenarios share rank 1                             -> NO_CLEAR_WINNER
  4. unique winner with adverse trade-offs, unresolved
     assumptions or unverified scenarios left out               -> CONDITIONALLY_RECOMMENDED
  5. otherwise                                                  -> RECOMMENDED
Human approval is always required: this engine never authorizes execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.comparison import ComparisonResult
from qorvexa.industrial.decision_intelligence.diff import assumption_codes
from qorvexa.industrial.decision_intelligence.models import Admitted, DecisionConfig, F, Rejection
from qorvexa.industrial.decision_intelligence.numeric import fmt
from qorvexa.industrial.decision_intelligence.ranking import RankingResult, RankingStatus
from qorvexa.industrial.decision_intelligence.tradeoffs import TradeOff


class RecommendationStatus(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    CONDITIONALLY_RECOMMENDED = "CONDITIONALLY_RECOMMENDED"
    NO_CLEAR_WINNER = "NO_CLEAR_WINNER"
    NO_VERIFIED_RECOMMENDATION = "NO_VERIFIED_RECOMMENDATION"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


class EvidenceCoverage(BaseModel):
    model_config = F
    scenarios_submitted: int
    admitted_solved: int
    admitted_not_solved: int  # e.g. verified infeasible
    rejected_untrusted: int
    excluded_incomparable: int
    verification_class: str  # NUMERICAL_GAP_VERIFIED | NONE
    exact_optimality_proof: Literal[False] = False


class Recommendation(BaseModel):
    model_config = F
    status: RecommendationStatus
    recommended_scenario_id: str | None
    decision_objective: str
    supporting_metrics: dict[str, Decimal]
    key_improvements: tuple[str, ...]
    negative_trade_offs: tuple[str, ...]
    binding_resources: tuple[str, ...]
    unresolved_assumptions: tuple[str, ...]
    conditions: tuple[str, ...]
    evidence_limitations: tuple[str, ...]
    reasons: tuple[str, ...]
    human_approval_required: Literal[True] = True
    human_approval_reasons: tuple[str, ...]
    coverage: EvidenceCoverage


_LIMITS = (
    "GAP_NUMERICAL: numerically verified primal/dual gap, not an exact optimality proof.",
    "No verified dual information: no shadow prices or opportunity costs are available.",
    "Scenario inputs are user-declared and unvalidated against real plant data.",
)
_APPROVAL = (
    "The engine supports a decision; it does not authorize or execute production changes.",
    "Costs, demand acceptance and risks outside the modeled LP are not represented.",
)
UNTRUSTED_CODES = {
    "NO_RESULT",
    "PROBLEM_MANIFEST_MISMATCH",
    "FORMULATION_INVALID",
    "FORMULATION_TAMPERED",
    "OBJECTIVE_MISMATCH",
    "MODEL_HASH_MISMATCH",
    "MISSING_LOWERING_EVIDENCE",
    "RESULT_FAILED",
    "RESULT_UNKNOWN",
    "VARIABLE_MAPPING_MISMATCH",
    "UNVERIFIED_RESULT",
    "UNIT_EVIDENCE_MISSING",
    "UNIT_EVIDENCE_INVALID",
    "UNIT_EVIDENCE_MISMATCH",
    "ADMISSION_MISSING",
    "ADMISSION_INVALID",
    "ADMISSION_NOT_VERIFIED",
}


def coverage_of(
    submitted: int,
    admitted: Sequence[Admitted],
    rejections: Sequence[Rejection],
    comparison: ComparisonResult | None,
) -> EvidenceCoverage:
    solved = sum(1 for a in admitted if a.solved)
    incomparable = (
        0
        if comparison is None
        else sum(1 for r in comparison.excluded if r.code not in UNTRUSTED_CODES)
    )
    return EvidenceCoverage(
        scenarios_submitted=submitted,
        admitted_solved=solved,
        admitted_not_solved=len(admitted) - solved,
        rejected_untrusted=sum(1 for r in rejections if r.code in UNTRUSTED_CODES),
        excluded_incomparable=incomparable,
        verification_class="NUMERICAL_GAP_VERIFIED" if solved else "NONE",
    )


def recommend(
    *,
    objective_text: str,
    baseline: Admitted | Rejection,
    solved: Mapping[str, Admitted],
    comparison: ComparisonResult | None,
    ranking: RankingResult | None,
    trade_offs: Mapping[str, tuple[TradeOff, ...]],
    config: DecisionConfig,
    coverage: EvidenceCoverage,
    untrusted: Sequence[Rejection],
) -> Recommendation:
    def out(
        status: RecommendationStatus,
        sid: str | None,
        reasons: list[str],
        *,
        metrics: dict[str, Decimal] | None = None,
        improvements: tuple[str, ...] = (),
        negatives: tuple[str, ...] = (),
        binding: tuple[str, ...] = (),
        unresolved: tuple[str, ...] = (),
        conditions: tuple[str, ...] = (),
    ) -> Recommendation:
        return Recommendation(
            status=status,
            recommended_scenario_id=sid,
            decision_objective=objective_text,
            supporting_metrics=metrics or {},
            key_improvements=improvements,
            negative_trade_offs=negatives,
            binding_resources=binding,
            unresolved_assumptions=unresolved,
            conditions=conditions,
            evidence_limitations=_LIMITS,
            reasons=tuple(reasons),
            human_approval_reasons=_APPROVAL,
            coverage=coverage,
        )

    if isinstance(baseline, Rejection) or not baseline.solved:
        why = (
            f"baseline rejected: {baseline.code}: {baseline.reason}"
            if isinstance(baseline, Rejection)
            else f"baseline status is {baseline.report.status.value}"
        )
        return out(RecommendationStatus.NO_VERIFIED_RECOMMENDATION, None, [why])
    if not solved or ranking is None:
        return out(RecommendationStatus.NO_VERIFIED_RECOMMENDATION, None, ["no solved candidate"])
    if ranking.status in (RankingStatus.MISSING_CONFIGURATION, RankingStatus.INVALID_CONFIGURATION):
        return out(
            RecommendationStatus.REQUIRES_HUMAN_REVIEW,
            None,
            [f"ranking not usable: {ranking.status.value}: {ranking.status_detail}"],
        )
    if ranking.status == RankingStatus.NO_ELIGIBLE_SCENARIOS:
        return out(
            RecommendationStatus.REQUIRES_HUMAN_REVIEW,
            None,
            ["no scenario satisfies the configured hard requirements", ranking.status_detail],
        )
    winners = ranking.winners
    if len(winners) != 1:
        return out(
            RecommendationStatus.NO_CLEAR_WINNER,
            None,
            ["tied at rank 1: " + ", ".join(winners)],
        )
    wid = winners[0]
    w = solved[wid]
    conditions: list[str] = []
    unresolved: list[str] = []
    negatives: list[str] = []
    improvements: list[str] = []
    if wid != baseline.run.scenario_id:
        diff_codes = []
        comp = next(
            (c for c in (comparison.compared if comparison else ()) if c.scenario_id == wid), None
        )
        if comp is not None:
            diff_codes = [
                c
                for c in assumption_codes(comp.input_diff)
                if c not in config.acknowledged_assumptions
            ]
            if comp.objective is not None:
                improvements.append(
                    f"objective {fmt(comp.objective.baseline)} -> {fmt(comp.objective.scenario)} "
                    f"{comp.objective.currency} ({comp.objective.effect.value.lower()})"
                )
            for p in comp.products:
                if p.absolute_change != 0 and abs(p.absolute_change) > Decimal("1e-6"):
                    improvements.append(
                        f"{p.product_id}: {fmt(p.baseline)} -> {fmt(p.scenario)} {p.unit}"
                    )
        unresolved.extend(diff_codes)
        for t in trade_offs.get(wid, ()):
            if t.state == "AVAILABLE" and t.adverse:
                negatives.append(t.summary)
        if negatives:
            conditions.append("adverse trade-offs must be accepted by a decision maker")
        if unresolved:
            conditions.append("unmodeled assumptions must be resolved or acknowledged")
    else:
        improvements.append("no tested scenario outperforms the baseline plan")
    if (
        isinstance(baseline, Admitted)
        and baseline.admission_status != "ADMITTED_UNITS_VERIFIED"
        and "UNIT_VALIDATION_NOT_PERFORMED" not in config.acknowledged_assumptions
    ):
        unresolved.append("UNIT_VALIDATION_NOT_PERFORMED")
        conditions.append(
            "units were not validated (explicit legacy admission); acknowledge to proceed"
        )
    if untrusted:
        conditions.append(
            f"{len(untrusted)} scenario(s) lacked verified results and were not considered"
        )
    status = (
        RecommendationStatus.CONDITIONALLY_RECOMMENDED
        if conditions
        else RecommendationStatus.RECOMMENDED
    )
    keys = ("objective", "total_volume", "mean_utilization", "bottleneck_count")
    return out(
        status,
        wid,
        [f"rank 1 under {ranking.method.value}"],
        metrics={k: w.metrics[k] for k in keys if k in w.metrics},
        improvements=tuple(improvements),
        negatives=tuple(negatives),
        binding=tuple(w.report.bottleneck_resource_ids),
        unresolved=tuple(unresolved),
        conditions=tuple(conditions),
    )
