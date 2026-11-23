"""Bottleneck-driven refinement with an explicit, checkable justification.

The only adjustment generated automatically: when a candidate violates its operational
requirements and a simulated bottleneck machine is bound to a time-measured planning resource whose
LP capacity EXCEEDS the scheduled productive time of its bound machines in the plan window
(shifts minus breaks, planned downtime and explicit breakdowns, computed from the twin calendar),
propose re-optimizing with that capacity. Capacity is never raised and never invented; when no
such evidence exists, no candidate is generated (the loop converges honestly).
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from qorvexa.industrial.co_orchestration.contracts import (
    Candidate,
    CandidateOrigin,
    CandidateSpec,
    CandidateStatus,
    ExperimentConfig,
)
from qorvexa.industrial.co_orchestration.evaluate import productive_seconds
from qorvexa.industrial.digital_twin import FactoryModel
from qorvexa.industrial.digital_twin.validation import compile_model
from qorvexa.industrial.production_planning import PlanningProblem
from qorvexa.industrial.production_planning.scenario import CapacityChange, ScenarioSpec
from qorvexa.industrial.units import Quantity, UnitError, parse_unit


def _exact_decimal(f: Fraction) -> Decimal | None:
    """Decimal only if the fraction terminates (no rounding)."""
    d = f.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d != 1:
        return None
    return Decimal(f.numerator) / Decimal(f.denominator)


def plan_window(twin: FactoryModel, cfg: ExperimentConfig) -> int | None:
    cm, _ = compile_model(twin)
    if cm is None:
        return None
    if cfg.conversion.due_at is None:
        return cm.horizon
    try:
        s = (
            Quantity.of(cfg.conversion.due_at.value, cfg.conversion.due_at.unit)
            .to("s")
            .result.value
        )
    except UnitError:
        return None
    return int(s) if s.denominator == 1 else None


def refinements(
    cand: Candidate, problem: PlanningProblem, twin: FactoryModel, cfg: ExperimentConfig
) -> list[CandidateSpec]:
    if cand.status is not CandidateStatus.OPERATIONAL_VIOLATIONS or cand.sim is None:
        return []
    window = plan_window(twin, cfg)
    if window is None:
        return []
    top = cand.sim.bottlenecks[:1]
    out: list[CandidateSpec] = []
    for b in cfg.bindings:
        if not set(top) & set(b.machine_ids):
            continue
        res = next((r for r in problem.resources if r.id == b.resource_id), None)
        if res is None:
            continue
        try:
            u = parse_unit(res.unit)
            if u.dimension != parse_unit("s").dimension:
                continue  # not a time resource: no calendar argument applies
            cap_s = Quantity.of(str(res.capacity), u).to("s").result.value
        except UnitError:
            continue
        machines = tuple(m for m in b.machine_ids if m in {x.machine_id for x in twin.machines})
        prod = productive_seconds(twin, machines, window)
        if prod is None or Fraction(prod) >= cap_s:
            continue
        new_cap_f = Quantity.of(prod, "s").to(u).result.value
        new_cap = _exact_decimal(new_cap_f)
        if new_cap is None:
            continue  # not exactly representable: refuse rather than round
        spec = ScenarioSpec(
            scenario_id=f"refine-{b.resource_id}-{cand.candidate_id[-8:]}",
            description="capacity limited to scheduled productive time of the bound machines",
            changes=(CapacityChange(resource_id=b.resource_id, capacity=new_cap),),
        )
        base_changes = cand.spec.planning.changes if cand.spec.planning else ()
        merged = tuple(
            c
            for c in base_changes
            if not (c.kind == "capacity" and getattr(c, "resource_id", None) == b.resource_id)
        )
        spec = spec.model_copy(update={"changes": (*merged, *spec.changes)})
        out.append(
            CandidateSpec(
                label=f"refine:{b.resource_id}@{cand.candidate_id[-8:]}",
                origin=CandidateOrigin.REFINEMENT,
                planning=spec,
                twin_changes=cand.spec.twin_changes,
                parent_id=cand.candidate_id,
                justification=(
                    f"simulated bottleneck {top[0]} realises {b.resource_id}; "
                    f"LP capacity {res.capacity} {res.unit} "
                    f"exceeds scheduled productive time {new_cap} {res.unit} "
                    f"of {list(machines)} in [0, {window}] s "
                    f"(violations: {[v.code for v in cand.violations]})"
                ),
            )
        )
    return out
