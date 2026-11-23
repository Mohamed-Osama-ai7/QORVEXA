"""Baseline vs scenario comparison from admitted (verified) evidence only."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.diff import InputDiff, diff_problems
from qorvexa.industrial.decision_intelligence.metrics import shortfall
from qorvexa.industrial.decision_intelligence.models import Admitted, F, Rejection
from qorvexa.industrial.decision_intelligence.numeric import (
    absolute_change,
    close,
    relative_change,
)
from qorvexa.industrial.production_planning.model import ObjectiveMode, PlanningProblem


class Effect(StrEnum):
    IMPROVED = "IMPROVED"
    DETERIORATED = "DETERIORATED"
    UNCHANGED = "UNCHANGED"


class ObjectiveChange(BaseModel):
    model_config = F
    mode: ObjectiveMode
    currency: str
    baseline: Decimal
    scenario: Decimal
    absolute_change: Decimal
    relative_change: Decimal | None
    relative_note: str
    effect: Effect


class ProductChange(BaseModel):
    model_config = F
    product_id: str
    unit: str
    baseline: Decimal
    scenario: Decimal
    absolute_change: Decimal
    relative_change: Decimal | None


class ResourceChange(BaseModel):
    model_config = F
    resource_id: str
    unit: str
    capacity_baseline: Decimal
    capacity_scenario: Decimal
    used_baseline: Decimal
    used_scenario: Decimal
    remaining_baseline: Decimal
    remaining_scenario: Decimal
    utilization_baseline: Decimal | None
    utilization_scenario: Decimal | None
    utilization_change: Decimal | None
    binding_baseline: bool
    binding_scenario: bool
    binding_change: str  # BECAME_BINDING | NO_LONGER_BINDING | UNCHANGED


class DemandChange(BaseModel):
    model_config = F
    product_id: str
    minimum_baseline: Decimal
    minimum_scenario: Decimal
    quantity_baseline: Decimal
    quantity_scenario: Decimal
    shortfall_baseline: Decimal
    shortfall_scenario: Decimal
    meets_minimum_scenario: bool


class ScenarioComparison(BaseModel):
    model_config = F
    scenario_id: str
    status: str  # ReportStatus value of the scenario
    baseline_status: str
    status_change: str | None
    objective: ObjectiveChange | None
    products: tuple[ProductChange, ...]
    resources: tuple[ResourceChange, ...]
    demand: tuple[DemandChange, ...]
    input_diff: InputDiff
    model_sha256: str
    input_sha256: str


class ComparisonResult(BaseModel):
    model_config = F
    baseline_id: str
    mode: ObjectiveMode
    currency: str
    period_label: str
    compared: tuple[ScenarioComparison, ...]
    excluded: tuple[Rejection, ...]


def _definition(p: PlanningProblem) -> tuple[object, object]:
    cons = {(c.resource_id, c.product_id, c.amount_per_unit) for c in p.consumptions}
    kinds = {(r.id, r.kind) for r in p.resources}
    return cons, kinds


def _compat(base: Admitted, o: Admitted) -> Rejection | None:
    bp, op = base.run.problem, o.run.problem
    sid = o.run.scenario_id

    def no(code: str, why: str) -> Rejection:
        return Rejection(scenario_id=sid, code=code, reason=why)

    if bp.mode != op.mode:
        return no("INCOMPATIBLE_OBJECTIVE", f"objective modes differ ({bp.mode} vs {op.mode})")
    if bp.currency != op.currency:
        return no("INCOMPATIBLE_CURRENCY", f"currencies differ ({bp.currency} vs {op.currency})")
    if bp.metadata.period_label != op.metadata.period_label:
        return no("INCOMPATIBLE_HORIZON", "planning horizons (period labels) differ")
    if {(p.id, p.unit) for p in bp.products} != {(p.id, p.unit) for p in op.products}:
        return no("INCOMPATIBLE_UNITS", "product sets or product units differ")
    if {(r.id, r.unit) for r in bp.resources} != {(r.id, r.unit) for r in op.resources}:
        return no("INCOMPATIBLE_UNITS", "resource sets or resource units differ")
    if _definition(bp) != _definition(op):
        return no(
            "INCOMPATIBLE_PROBLEM",
            "consumption coefficients or resource kinds differ: not a what-if of this baseline",
        )
    lin = o.run.formulation.manifest.lineage
    if (
        f"base_input_sha256:{base.input_sha256}" not in lin
        or f"scenario:{sid}" not in lin
        or o.model_sha256 == base.model_sha256
        or o.input_sha256 == base.input_sha256
    ):
        return no(
            "LINEAGE_MISMATCH",
            "scenario lineage does not tie it to this baseline, or it is not a distinct model",
        )
    return None


def _effect(mode: ObjectiveMode, base: Decimal, new: Decimal) -> Effect:
    if close(base, new):
        return Effect.UNCHANGED
    better = new > base if mode is ObjectiveMode.MAXIMIZE_MARGIN else new < base
    return Effect.IMPROVED if better else Effect.DETERIORATED


def compare(baseline: Admitted, others: Sequence[Admitted | Rejection]) -> ComparisonResult:
    bp = baseline.run.problem
    compared: list[ScenarioComparison] = []
    excluded: list[Rejection] = []
    for o in others:
        if isinstance(o, Rejection):
            excluded.append(o)
            continue
        bad = _compat(baseline, o)
        if bad is not None:
            excluded.append(bad)
            continue
        compared.append(_one(baseline, o))
    return ComparisonResult(
        baseline_id=baseline.run.scenario_id,
        mode=bp.mode,
        currency=bp.currency,
        period_label=bp.metadata.period_label,
        compared=tuple(sorted(compared, key=lambda c: c.scenario_id)),
        excluded=tuple(sorted(excluded, key=lambda r: (r.scenario_id, r.code))),
    )


def _one(base: Admitted, o: Admitted) -> ScenarioComparison:
    bstat, ostat = base.report.status.value, o.report.status.value
    change = None if bstat == ostat else f"baseline {bstat} -> scenario {ostat}"
    diff = diff_problems(base.run.problem, o.run.problem)
    common = dict(
        scenario_id=o.run.scenario_id,
        status=ostat,
        baseline_status=bstat,
        status_change=change,
        input_diff=diff,
        model_sha256=o.model_sha256,
        input_sha256=o.input_sha256,
    )
    if not (base.solved and o.solved):
        return ScenarioComparison(objective=None, products=(), resources=(), demand=(), **common)
    mode = base.run.problem.mode
    bo, so = base.metrics["objective"], o.metrics["objective"]
    rel = relative_change(bo, so)
    obj = ObjectiveChange(
        mode=mode,
        currency=base.run.problem.currency,
        baseline=bo,
        scenario=so,
        absolute_change=absolute_change(bo, so),
        relative_change=rel,
        relative_note="relative change undefined: baseline objective is zero"
        if rel is None
        else "relative to |baseline objective|",
        effect=_effect(mode, bo, so),
    )
    bq = {q.product_id: q for q in base.report.quantities}
    products = tuple(
        ProductChange(
            product_id=pid,
            unit=bq[pid].unit,
            baseline=bq[pid].quantity,
            scenario=q.quantity,
            absolute_change=absolute_change(bq[pid].quantity, q.quantity),
            relative_change=relative_change(bq[pid].quantity, q.quantity),
        )
        for pid, q in sorted((q.product_id, q) for q in o.report.quantities)
    )
    bu = {u.resource_id: u for u in base.report.utilization}
    resources: list[ResourceChange] = []
    for u in sorted(o.report.utilization, key=lambda z: z.resource_id):
        b = bu[u.resource_id]
        uc = (
            None
            if b.utilization is None or u.utilization is None
            else absolute_change(b.utilization, u.utilization)
        )
        kind = (
            "BECAME_BINDING"
            if u.at_capacity and not b.at_capacity
            else "NO_LONGER_BINDING"
            if b.at_capacity and not u.at_capacity
            else "UNCHANGED"
        )
        resources.append(
            ResourceChange(
                resource_id=u.resource_id,
                unit=u.unit,
                capacity_baseline=b.capacity,
                capacity_scenario=u.capacity,
                used_baseline=b.used,
                used_scenario=u.used,
                remaining_baseline=b.remaining,
                remaining_scenario=u.remaining,
                utilization_baseline=b.utilization,
                utilization_scenario=u.utilization,
                utilization_change=uc,
                binding_baseline=b.at_capacity,
                binding_scenario=u.at_capacity,
                binding_change=kind,
            )
        )
    bd = {d.product_id: d for d in base.report.demand}
    demand = tuple(
        DemandChange(
            product_id=d.product_id,
            minimum_baseline=bd[d.product_id].minimum,
            minimum_scenario=d.minimum,
            quantity_baseline=bd[d.product_id].quantity,
            quantity_scenario=d.quantity,
            shortfall_baseline=shortfall(bd[d.product_id].quantity, bd[d.product_id].minimum),
            shortfall_scenario=shortfall(d.quantity, d.minimum),
            meets_minimum_scenario=d.meets_minimum,
        )
        for d in sorted(o.report.demand, key=lambda z: z.product_id)
    )
    return ScenarioComparison(
        objective=obj,
        products=products,
        resources=tuple(resources),
        demand=demand,
        **common,
    )
