"""Pareto dominance over solved, compatible scenarios (tolerance-aware ties)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.models import F, MetricDirection, MetricObjective
from qorvexa.industrial.decision_intelligence.numeric import close, fmt


class ParetoStatus(StrEnum):
    OK = "OK"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    NO_ELIGIBLE_SCENARIOS = "NO_ELIGIBLE_SCENARIOS"


class Dominated(BaseModel):
    model_config = F
    scenario_id: str
    dominated_by: tuple[str, ...]
    explanation: str


class ParetoResult(BaseModel):
    model_config = F
    status: ParetoStatus
    status_detail: str
    objectives: tuple[MetricObjective, ...]
    non_dominated: tuple[str, ...]
    dominated: tuple[Dominated, ...]
    equal_groups: tuple[tuple[str, ...], ...]
    excluded: tuple[tuple[str, str], ...]
    note: str = (
        "Pareto-optimal means not dominated on the chosen metrics; it is not universally best."
    )


def _cmp(a: Decimal, b: Decimal, d: MetricDirection) -> int:
    """+1 if a better than b, -1 if worse, 0 if equal within tolerance."""
    if close(a, b):
        return 0
    better = a > b if d is MetricDirection.MAXIMIZE else a < b
    return 1 if better else -1


def pareto(
    candidates: Mapping[str, Mapping[str, Decimal]], objectives: Sequence[MetricObjective]
) -> ParetoResult:
    objs = tuple(objectives)

    def stop(status: ParetoStatus, detail: str, ex: Sequence[tuple[str, str]] = ()) -> ParetoResult:
        return ParetoResult(
            status=status,
            status_detail=detail,
            objectives=objs,
            non_dominated=(),
            dominated=(),
            equal_groups=(),
            excluded=tuple(ex),
        )

    if not objs:
        return stop(ParetoStatus.NOT_CONFIGURED, "no Pareto objectives were configured")
    if len({o.metric for o in objs}) != len(objs):
        return stop(ParetoStatus.INVALID_CONFIGURATION, "duplicate metric in Pareto objectives")
    excluded: list[tuple[str, str]] = []
    elig: dict[str, Mapping[str, Decimal]] = {}
    for sid in sorted(candidates):
        lack = [o.metric for o in objs if o.metric not in candidates[sid]]
        if lack:
            excluded.append((sid, "metric unavailable: " + ", ".join(lack)))
        else:
            elig[sid] = candidates[sid]
    if not elig:
        return stop(
            ParetoStatus.NO_ELIGIBLE_SCENARIOS, "no scenario has every Pareto metric", excluded
        )

    def dominates(a: str, b: str) -> bool:
        res = [_cmp(elig[a][o.metric], elig[b][o.metric], o.direction) for o in objs]
        return all(r >= 0 for r in res) and any(r > 0 for r in res)

    ids = sorted(elig)
    dominated: list[Dominated] = []
    for b in ids:
        doms = tuple(a for a in ids if a != b and dominates(a, b))
        if doms:
            a = doms[0]
            parts = [
                f"{o.metric}: {a} {fmt(elig[a][o.metric])} vs {b} {fmt(elig[b][o.metric])} "
                f"({o.direction.value.lower()})"
                for o in objs
            ]
            dominated.append(
                Dominated(
                    scenario_id=b,
                    dominated_by=doms,
                    explanation=f"{a} is at least as good on every metric and strictly better on "
                    "at least one: " + "; ".join(parts),
                )
            )
    dom_ids = {d.scenario_id for d in dominated}
    nondom = tuple(s for s in ids if s not in dom_ids)
    groups: list[tuple[str, ...]] = []
    used: set[str] = set()
    for a in nondom:
        if a in used:
            continue
        same = tuple(
            b
            for b in nondom
            if all(_cmp(elig[a][o.metric], elig[b][o.metric], o.direction) == 0 for o in objs)
        )
        used.update(same)
        if len(same) > 1:
            groups.append(same)
    return ParetoResult(
        status=ParetoStatus.OK,
        status_detail="dominance computed with tolerance-aware equality",
        objectives=objs,
        non_dominated=nondom,
        dominated=tuple(dominated),
        equal_groups=tuple(groups),
        excluded=tuple(excluded),
    )
