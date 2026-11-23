"""Immutable baselines, explicit what-if scenarios and result comparison.

A scenario names the exact baseline (model hash), lists typed changes, gets its own hash and is
validated and simulated independently; it never reuses a baseline result.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import (
    AddBreakdown,
    AddMachine,
    AddPlannedDowntime,
    FactoryModel,
    Operation,
    Policies,
    RemoveBreakdowns,
    Routing,
    Scenario,
    SetBufferCapacity,
    SetCalendar,
    SetDispatchPolicy,
    SetOrderQuantity,
    SetProcessingTime,
    SourceRef,
)
from qorvexa.industrial.digital_twin.metrics import Ratio
from qorvexa.industrial.digital_twin.report import ReportStatus, SimulationReport, simulate
from qorvexa.industrial.digital_twin.validation import model_sha256

F = ConfigDict(frozen=True, extra="forbid")


class ScenarioError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def scenario_sha256(s: Scenario) -> str:
    return hashlib.sha256(
        json.dumps(s.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def _map_ops(model: FactoryModel, fn: object) -> tuple[Routing, ...]:
    return tuple(
        r.model_copy(update={"operations": tuple(fn(op) for op in r.operations)})  # type: ignore[operator]
        for r in model.routings
    )


def apply_scenario(baseline: FactoryModel, s: Scenario) -> FactoryModel:
    """Return a NEW model; the baseline object is never modified."""
    if model_sha256(baseline) != s.baseline_model_sha256:
        raise ScenarioError(
            "BASELINE_MISMATCH", "scenario was written for a different baseline model"
        )
    m = baseline
    op_ids = {op.op_id for r in m.routings for op in r.operations}
    for c in s.changes:
        if isinstance(c, SetProcessingTime):
            if c.op_id not in op_ids:
                raise ScenarioError("UNKNOWN_OPERATION", c.op_id)
            proc = c.processing
            m = m.model_copy(
                update={
                    "routings": _map_ops(
                        m,
                        lambda op, oid=c.op_id, p=proc: (
                            op.model_copy(update={"processing": p}) if op.op_id == oid else op
                        ),
                    )
                }
            )
        elif isinstance(c, AddMachine):
            if any(x.machine_id == c.machine.machine_id for x in m.machines):
                raise ScenarioError("DUPLICATE_MACHINE", c.machine.machine_id)
            missing = set(c.eligible_for) - op_ids
            if missing or not c.eligible_for:
                raise ScenarioError("UNKNOWN_OPERATION", f"{sorted(missing)}")
            mid, ops = c.machine.machine_id, set(c.eligible_for)

            def add(op: Operation, mid: str = mid, ops: set[str] = ops) -> Operation:
                return (
                    op.model_copy(update={"machines": (*op.machines, mid)})
                    if op.op_id in ops
                    else op
                )

            m = m.model_copy(
                update={"machines": (*m.machines, c.machine), "routings": _map_ops(m, add)}
            )
        elif isinstance(c, SetBufferCapacity):
            if not any(b.buffer_id == c.buffer_id for b in m.buffers):
                raise ScenarioError("UNKNOWN_BUFFER", c.buffer_id)
            m = m.model_copy(
                update={
                    "buffers": tuple(
                        b.model_copy(update={"capacity": c.capacity})
                        if b.buffer_id == c.buffer_id
                        else b
                        for b in m.buffers
                    )
                }
            )
        elif isinstance(c, AddPlannedDowntime):
            if not any(x.machine_id == c.machine_id for x in m.machines):
                raise ScenarioError("UNKNOWN_MACHINE", c.machine_id)
            m = m.model_copy(
                update={
                    "machines": tuple(
                        x.model_copy(update={"planned_downtime": (*x.planned_downtime, c.interval)})
                        if x.machine_id == c.machine_id
                        else x
                        for x in m.machines
                    )
                }
            )
        elif isinstance(c, AddBreakdown):
            m = m.model_copy(update={"breakdowns": (*m.breakdowns, c.breakdown)})
        elif isinstance(c, RemoveBreakdowns):
            m = m.model_copy(
                update={
                    "breakdowns": tuple(b for b in m.breakdowns if b.machine_id != c.machine_id)
                }
            )
        elif isinstance(c, SetDispatchPolicy):
            m = m.model_copy(
                update={"policies": m.policies.model_copy(update={"dispatch": c.dispatch})}
            )
        elif isinstance(c, SetOrderQuantity):
            if not any(o.order_id == c.order_id for o in m.orders):
                raise ScenarioError("UNKNOWN_ORDER", c.order_id)
            m = m.model_copy(
                update={
                    "orders": tuple(
                        o.model_copy(update={"quantity": c.quantity})
                        if o.order_id == c.order_id
                        else o
                        for o in m.orders
                    )
                }
            )
        elif isinstance(c, SetCalendar):
            if not any(x.calendar_id == c.calendar.calendar_id for x in m.calendars):
                raise ScenarioError("UNKNOWN_CALENDAR", c.calendar.calendar_id)
            m = m.model_copy(
                update={
                    "calendars": tuple(
                        c.calendar if x.calendar_id == c.calendar.calendar_id else x
                        for x in m.calendars
                    )
                }
            )
    assert isinstance(m.policies, Policies)  # noqa: S101
    return m.model_copy(
        update={
            "model_id": f"{baseline.model_id}::{s.scenario_id}",
            "sources": (
                *baseline.sources,
                SourceRef(kind="scenario", ref=s.scenario_id, sha256=scenario_sha256(s)),
            ),
        }
    )


def simulate_scenario(baseline: FactoryModel, s: Scenario, **kw: object) -> SimulationReport:
    model = apply_scenario(baseline, s)
    return simulate(
        model, scenario_id=s.scenario_id, scenario_sha256=scenario_sha256(s),
        baseline_model_sha256=s.baseline_model_sha256, **kw,  # type: ignore[arg-type]
    )  # fmt: skip


# ----------------------------------------------------------------------------- comparison
class Delta(BaseModel):
    model_config = F
    metric: str
    baseline: str | None
    scenario: str | None
    change: str | None  # scenario - baseline
    relative: str | None  # change / baseline


class Comparison(BaseModel):
    model_config = F
    baseline_simulation_id: str
    scenario_simulation_id: str
    baseline_model_sha256: str
    scenario_sha256: str | None
    kind: str
    deltas: tuple[Delta, ...]
    bottleneck_baseline: tuple[str, ...]
    bottleneck_scenario: tuple[str, ...]
    notes: tuple[str, ...]


def _d(name: str, a: Ratio | int | None, b: Ratio | int | None) -> Delta:
    from decimal import Decimal

    def dec(x: Ratio | int | None) -> Decimal | None:
        if x is None:
            return None
        return Decimal(x) if isinstance(x, int) else Decimal(x.num) / Decimal(x.den)

    da, db = dec(a), dec(b)
    q = Decimal("0.000001")
    change = (db - da).quantize(q) if da is not None and db is not None else None
    rel = (change / da).quantize(q) if change is not None and da not in (None, 0) else None
    return Delta(
        metric=name,
        baseline=str(da.quantize(q)) if da is not None else None,
        scenario=str(db.quantize(q)) if db is not None else None,
        change=str(change) if change is not None else None,
        relative=str(rel) if rel is not None else None,
    )


def compare(baseline: SimulationReport, scenario: SimulationReport) -> Comparison:
    if (
        baseline.status is ReportStatus.INVALID_INPUT
        or scenario.status is ReportStatus.INVALID_INPUT
    ):
        raise ScenarioError("NOT_COMPARABLE", "an invalid run has no results")
    if scenario.baseline_model_sha256 != baseline.model_sha256:
        raise ScenarioError("LINEAGE_MISMATCH", "the scenario was not derived from this baseline")
    if baseline.kind != scenario.kind:
        raise ScenarioError(
            "KIND_MISMATCH", "deterministic results cannot be compared with estimates"
        )
    if baseline.parameters.seed != scenario.parameters.seed or (
        baseline.parameters.replications != scenario.parameters.replications
    ):
        raise ScenarioError("PARAMETER_MISMATCH", "use the same seed and replication count")
    a, b = baseline.metrics, scenario.metrics
    deltas = [
        _d("completed", a.completed, b.completed),
        _d("throughput_per_hour", a.throughput_per_hour, b.throughput_per_hour),
        _d("wip_avg", a.wip_avg, b.wip_avg),
        _d("avg_lead_time_s", a.avg_lead_time_s, b.avg_lead_time_s),
        _d("avg_cycle_time_s", a.avg_cycle_time_s, b.avg_cycle_time_s),
        _d("avg_wait_s", a.avg_wait_s, b.avg_wait_s),
        _d("late_orders", a.late_orders, b.late_orders),
        _d("window_s", a.window_s, b.window_s),
    ]
    mids = sorted({m.machine_id for m in a.machines} | {m.machine_id for m in b.machines})
    am, bm = {m.machine_id: m for m in a.machines}, {m.machine_id: m for m in b.machines}
    for mid in mids:
        deltas.append(
            _d(
                f"utilization:{mid}",
                am[mid].utilization if mid in am else None,
                bm[mid].utilization if mid in bm else None,
            )
        )
        deltas.append(
            _d(
                f"down_s:{mid}",
                am[mid].down_s if mid in am else None,
                bm[mid].down_s if mid in bm else None,
            )
        )
        deltas.append(
            _d(
                f"blocked_s:{mid}",
                am[mid].blocked_s if mid in am else None,
                bm[mid].blocked_s if mid in bm else None,
            )
        )
    notes = []
    if baseline.status is not scenario.status:
        notes.append(f"run status differs: {baseline.status.value} vs {scenario.status.value}")
    if baseline.kind and baseline.kind.value == "STOCHASTIC_ESTIMATE":
        notes.append("first-replication metrics; use the summaries for distributions")
    return Comparison(
        baseline_simulation_id=baseline.simulation_id,
        scenario_simulation_id=scenario.simulation_id,
        baseline_model_sha256=baseline.model_sha256,
        scenario_sha256=scenario.scenario_sha256,
        kind=str(baseline.kind.value if baseline.kind else ""),
        deltas=tuple(deltas),
        bottleneck_baseline=tuple(x.machine_id for x in a.bottlenecks),
        bottleneck_scenario=tuple(x.machine_id for x in b.bottlenecks),
        notes=tuple(notes),
    )
