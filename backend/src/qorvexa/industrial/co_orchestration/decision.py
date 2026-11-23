"""Decision evaluation over operationally feasible candidates (reuses Decision Intelligence).

Pareto analysis and ranking are Decision Intelligence's ``pareto`` and ``rank_scenarios`` applied to
combined metrics (verified objective + simulated operational metrics). Only candidates with a
verified optimization result AND a complete simulation without violations are eligible. Nothing is
approved here.
"""

from __future__ import annotations

from decimal import Decimal

from qorvexa.industrial.co_orchestration.contracts import (
    Candidate,
    CandidateStatus,
    DecisionSpec,
    Recommendation,
    RecommendationStatus,
)
from qorvexa.industrial.decision_intelligence.pareto import ParetoResult, pareto
from qorvexa.industrial.decision_intelligence.ranking import (
    RankingResult,
    RankingStatus,
    rank_scenarios,
)
from qorvexa.industrial.production_planning import ObjectiveMode


def decide(
    candidates: list[Candidate],
    spec: DecisionSpec,
    mode: ObjectiveMode,
    baseline_id: str | None,
) -> tuple[ParetoResult | None, RankingResult | None, Recommendation]:
    feasible = {
        c.candidate_id: c
        for c in candidates
        if c.status is CandidateStatus.OPERATIONALLY_FEASIBLE
        and c.sim is not None
        and c.math
        and c.math.verified
    }
    base = next((c for c in candidates if c.candidate_id == baseline_id), None)
    if not feasible:
        why = [
            f"{c.candidate_id} ({c.spec.label}): {c.status.value}"
            + (f" {[v.code for v in c.violations]}" if c.violations else "")
            for c in candidates
            if c.status is not CandidateStatus.DUPLICATE
        ]
        status = (
            RecommendationStatus.BASELINE_NOT_VERIFIED
            if base is not None and not (base.math and base.math.verified) and len(candidates) == 1
            else RecommendationStatus.NO_OPERATIONALLY_FEASIBLE_CANDIDATE
        )
        return (
            None,
            None,
            Recommendation(
                status=status,
                candidate_id=None,
                rationale=tuple(why) or ("no candidate was evaluated",),
                trade_offs=(),
                uncertainty=(),
            ),
        )
    metrics = {cid: dict(c.sim.metrics) for cid, c in feasible.items() if c.sim is not None}
    par = pareto(metrics, spec.pareto_objectives)
    ranking = rank_scenarios(
        mode, spec.ranking_mode, metrics, spec.hard_constraints, spec.preferences
    )
    if ranking.status is not RankingStatus.OK or not ranking.entries:
        return (
            par,
            ranking,
            Recommendation(
                status=RecommendationStatus.NO_OPERATIONALLY_FEASIBLE_CANDIDATE,
                candidate_id=None,
                rationale=(f"ranking {ranking.status.value}: {ranking.status_detail}",),
                trade_offs=(),
                uncertainty=(),
            ),
        )
    winners = ranking.winners
    unc = _uncertainty(feasible[w] for w in winners)
    if len(winners) > 1:
        return (
            par,
            ranking,
            Recommendation(
                status=RecommendationStatus.TIE_REQUIRES_HUMAN_CHOICE,
                candidate_id=None,
                rationale=(f"tie between {list(winners)} under {spec.ranking_mode.value}",),
                trade_offs=_trade_offs(feasible[winners[0]], candidates),
                uncertainty=unc,
            ),
        )
    win = feasible[winners[0]]
    if win.contradictions:
        return (
            par,
            ranking,
            Recommendation(
                status=RecommendationStatus.BLOCKED_BY_CONTRADICTIONS,
                candidate_id=win.candidate_id,
                rationale=tuple(
                    f"{x.code} {x.resource_id}/{x.product_id}: "
                    f"planning {x.planning_value} vs twin {x.twin_value}"
                    for x in win.contradictions
                ),
                trade_offs=_trade_offs(win, candidates),
                uncertainty=unc,
            ),
        )
    rationale = [
        f"{win.candidate_id} ({win.spec.label}) ranks first by {ranking.method.value} "
        f"among {len(feasible)} operationally feasible candidate(s)",
        f"verified objective {win.math.objective if win.math else '?'}; "
        f"simulated makespan {win.sim.metrics.get('sim_makespan_s') if win.sim else '?'} s, "
        f"late orders {win.sim.metrics.get('sim_late_orders') if win.sim else '?'}",
    ]
    if win.spec.justification:
        rationale.append(f"derived because: {win.spec.justification}")
    if base is not None and base.candidate_id != win.candidate_id:
        rationale.append(
            f"the mathematically optimal baseline {base.candidate_id} is {base.status.value}"
            + (f" ({[v.code for v in base.violations]})" if base.violations else "")
        )
    return (
        par,
        ranking,
        Recommendation(
            status=RecommendationStatus.RECOMMENDED_FOR_HUMAN_REVIEW,
            candidate_id=win.candidate_id,
            rationale=tuple(rationale),
            trade_offs=_trade_offs(win, candidates),
            uncertainty=unc,
        ),
    )


def _trade_offs(win: Candidate, all_c: list[Candidate]) -> tuple[str, ...]:
    out = []
    w_obj = Decimal(win.math.objective) if win.math and win.math.objective else None
    for c in all_c:
        if c.candidate_id == win.candidate_id or not (
            c.math and c.math.verified and c.math.objective
        ):
            continue
        d = Decimal(c.math.objective) - w_obj if w_obj is not None else None
        tag = f"{c.candidate_id} ({c.spec.label}): objective {c.math.objective}"
        if d is not None:
            tag += f" ({'+' if d >= 0 else ''}{d} vs recommended)"
        tag += f", status {c.status.value}"
        if c.violations:
            tag += f", violations {[f'{v.code}={v.value}' for v in c.violations]}"
        out.append(tag)
    return tuple(out)


def _uncertainty(cands) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    out = []
    for c in cands:
        if c.sim is None:
            continue
        if c.sim.kind == "DETERMINISTIC_SIMULATION":
            out.append(
                f"{c.candidate_id}: deterministic simulation; no sampling uncertainty, "
                "model assumptions still apply"
            )
        for k, (mean, lo, hi) in sorted(c.sim.uncertainty.items()):
            out.append(
                f"{c.candidate_id} {k}: mean {mean}"
                + (f", 95% CI [{lo}, {hi}]" if lo else ", no interval (too few replications)")
            )
    return tuple(out)
