"""Transparent rankings over solved, compatible scenarios (raw metrics always visible).

Exact Decimal arithmetic. Hard constraints are applied first and can never be outweighed by
preferences. Missing metrics exclude a scenario (they are never treated as zero). Weighted scores
use min-max normalization per metric over eligible scenarios, so mixed units never meet raw.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.models import (
    F,
    HardConstraint,
    MetricDirection,
    Preference,
    RankingMode,
)
from qorvexa.industrial.decision_intelligence.numeric import CTX, ZERO, close
from qorvexa.industrial.production_planning.model import ObjectiveMode


class RankingStatus(StrEnum):
    OK = "OK"
    MISSING_CONFIGURATION = "MISSING_CONFIGURATION"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    NO_ELIGIBLE_SCENARIOS = "NO_ELIGIBLE_SCENARIOS"


class Contribution(BaseModel):
    model_config = F
    metric: str
    direction: MetricDirection
    raw: Decimal
    normalized: Decimal | None  # None when the metric has zero range (non-discriminating)
    weight: Decimal
    contribution: Decimal


class ConstraintCheck(BaseModel):
    model_config = F
    metric: str
    op: str
    threshold: Decimal
    value: Decimal | None
    satisfied: bool
    slack: Decimal | None


class RankedEntry(BaseModel):
    model_config = F
    rank: int
    scenario_id: str
    metrics: dict[str, Decimal]
    score: Decimal | None
    contributions: tuple[Contribution, ...]
    constraint_checks: tuple[ConstraintCheck, ...]


class RankingRejection(BaseModel):
    model_config = F
    scenario_id: str
    reasons: tuple[str, ...]


class RankingResult(BaseModel):
    model_config = F
    method: RankingMode
    status: RankingStatus
    status_detail: str
    entries: tuple[RankedEntry, ...]
    rejected: tuple[RankingRejection, ...]
    weights: tuple[tuple[str, MetricDirection, Decimal], ...] = ()
    normalization: str | None = None

    @property
    def winners(self) -> tuple[str, ...]:
        return tuple(sorted(e.scenario_id for e in self.entries if e.rank == 1))


Metrics = Mapping[str, Decimal]


def _checks(m: Metrics, hard: Sequence[HardConstraint]) -> tuple[ConstraintCheck, ...]:
    out: list[ConstraintCheck] = []
    for h in hard:
        v = m.get(h.metric)
        if v is None:
            out.append(
                ConstraintCheck(
                    metric=h.metric,
                    op=h.op,
                    threshold=h.threshold,
                    value=None,
                    satisfied=False,
                    slack=None,
                )
            )
            continue
        tol = Decimal("1e-6") * max(Decimal(1), abs(h.threshold))
        if h.op == "<=":
            ok, slack = v <= h.threshold + tol, h.threshold - v
        else:
            ok, slack = v >= h.threshold - tol, v - h.threshold
        out.append(
            ConstraintCheck(
                metric=h.metric, op=h.op, threshold=h.threshold, value=v, satisfied=ok, slack=slack
            )
        )
    return tuple(out)


def _filter(
    cands: Mapping[str, Metrics], hard: Sequence[HardConstraint]
) -> tuple[dict[str, tuple[ConstraintCheck, ...]], list[RankingRejection]]:
    ok: dict[str, tuple[ConstraintCheck, ...]] = {}
    rej: list[RankingRejection] = []
    for sid in sorted(cands):
        checks = _checks(cands[sid], hard)
        bad = [
            f"{c.metric} {c.op} {c.threshold}: "
            + ("metric unavailable" if c.value is None else f"value {c.value}")
            for c in checks
            if not c.satisfied
        ]
        if bad:
            rej.append(RankingRejection(scenario_id=sid, reasons=tuple(bad)))
        else:
            ok[sid] = checks
    return ok, rej


def _competition_ranks(order: list[tuple[str, Decimal]]) -> dict[str, int]:
    """Standard competition ranking (1,1,3) with tolerance ties; order is best first."""
    ranks: dict[str, int] = {}
    head: Decimal | None = None
    head_rank = 0
    for i, (sid, v) in enumerate(order, start=1):
        if head is None or not close(head, v):
            head, head_rank = v, i
        ranks[sid] = head_rank
    return ranks


def _validate_hard(hard: Sequence[HardConstraint]) -> str | None:
    seen = set()
    for h in hard:
        key = (h.metric, h.op)
        if key in seen:
            return f"duplicate hard constraint {key}"
        seen.add(key)
    return None


def rank_scenarios(
    mode: ObjectiveMode,
    ranking_mode: RankingMode,
    candidates: Mapping[str, Metrics],
    hard: Sequence[HardConstraint] = (),
    preferences: Sequence[Preference] = (),
) -> RankingResult:
    def empty(
        status: RankingStatus, detail: str, rej: Sequence[RankingRejection] = ()
    ) -> RankingResult:
        return RankingResult(
            method=ranking_mode,
            status=status,
            status_detail=detail,
            entries=(),
            rejected=tuple(rej),
        )

    problem = _validate_hard(hard)
    if problem:
        return empty(RankingStatus.INVALID_CONFIGURATION, problem)
    if ranking_mode is RankingMode.CONSTRAINT_FIRST and not hard:
        return empty(
            RankingStatus.MISSING_CONFIGURATION, "constraint-first ranking needs hard constraints"
        )
    prefs = tuple(preferences)
    if ranking_mode is RankingMode.WEIGHTED_PREFERENCE:
        if not prefs:
            return empty(
                RankingStatus.MISSING_CONFIGURATION, "weighted ranking needs explicit preferences"
            )
        for p in prefs:
            if p.weight <= 0:
                return empty(
                    RankingStatus.INVALID_CONFIGURATION, f"weight for {p.metric} must be > 0"
                )
        if len({p.metric for p in prefs}) != len(prefs):
            return empty(RankingStatus.INVALID_CONFIGURATION, "duplicate preference metric")
    eligible, rejected = _filter(candidates, hard)
    if not eligible:
        return empty(
            RankingStatus.NO_ELIGIBLE_SCENARIOS,
            "no scenario satisfies the hard requirements",
            rejected,
        )

    if ranking_mode is not RankingMode.WEIGHTED_PREFERENCE:
        higher = mode is ObjectiveMode.MAXIMIZE_MARGIN
        missing = [s for s in eligible if "objective" not in candidates[s]]
        for s in missing:
            rejected.append(
                RankingRejection(scenario_id=s, reasons=("objective metric unavailable",))
            )
            del eligible[s]
        if not eligible:
            return empty(
                RankingStatus.NO_ELIGIBLE_SCENARIOS, "no scenario has an objective", rejected
            )
        order = sorted(
            ((s, candidates[s]["objective"]) for s in eligible),
            key=lambda t: (-t[1] if higher else t[1], t[0]),
        )
        ranks = _competition_ranks(order)
        entries = tuple(
            RankedEntry(
                rank=ranks[s],
                scenario_id=s,
                metrics=dict(candidates[s]),
                score=None,
                contributions=(),
                constraint_checks=eligible[s],
            )
            for s, _ in order
        )
        return RankingResult(
            method=ranking_mode,
            status=RankingStatus.OK,
            status_detail="ranked by verified objective (hard constraints first)",
            entries=entries,
            rejected=tuple(rejected),
        )

    # weighted preference
    usable: dict[str, tuple[ConstraintCheck, ...]] = {}
    for s, checks in eligible.items():
        lacking = [p.metric for p in prefs if p.metric not in candidates[s]]
        if lacking:
            rejected.append(
                RankingRejection(
                    scenario_id=s, reasons=tuple(f"metric unavailable: {m}" for m in lacking)
                )
            )
        else:
            usable[s] = checks
    if not usable:
        return empty(
            RankingStatus.NO_ELIGIBLE_SCENARIOS, "no scenario has every weighted metric", rejected
        )
    total_w = sum((p.weight for p in prefs), ZERO)
    rng: dict[str, tuple[Decimal, Decimal]] = {}
    for p in prefs:
        vals = [candidates[s][p.metric] for s in usable]
        rng[p.metric] = (min(vals), max(vals))
    scored: list[tuple[str, Decimal, tuple[Contribution, ...]]] = []
    for s in sorted(usable):
        parts: list[Contribution] = []
        score = ZERO
        for p in prefs:
            lo, hi = rng[p.metric]
            raw = candidates[s][p.metric]
            if close(lo, hi):
                norm: Decimal | None = None
                contrib = ZERO
            else:
                span = hi - lo
                norm = (
                    CTX.divide(raw - lo, span)
                    if p.direction is MetricDirection.MAXIMIZE
                    else CTX.divide(hi - raw, span)
                )
                contrib = CTX.divide(CTX.multiply(p.weight, norm), total_w)
            score += contrib
            parts.append(
                Contribution(
                    metric=p.metric,
                    direction=p.direction,
                    raw=raw,
                    normalized=norm,
                    weight=p.weight,
                    contribution=contrib,
                )
            )
        scored.append((s, score, tuple(parts)))
    scored.sort(key=lambda t: (-t[1], t[0]))
    ranks = _competition_ranks([(s, sc) for s, sc, _ in scored])
    entries = tuple(
        RankedEntry(
            rank=ranks[s],
            scenario_id=s,
            metrics={p.metric: candidates[s][p.metric] for p in prefs} | dict(candidates[s]),
            score=sc,
            contributions=parts,
            constraint_checks=usable[s],
        )
        for s, sc, parts in scored
    )
    return RankingResult(
        method=ranking_mode,
        status=RankingStatus.OK,
        status_detail="weighted min-max score; hard constraints applied first and never outweighed",
        entries=entries,
        rejected=tuple(rejected),
        weights=tuple((p.metric, p.direction, p.weight) for p in prefs),
        normalization=(
            "per-metric min-max over eligible scenarios: (v-min)/(max-min) for MAXIMIZE, "
            "(max-v)/(max-min) for MINIMIZE; zero-range metrics contribute 0 and are flagged; "
            "score = sum(weight*normalized)/sum(weights)"
        ),
    )
