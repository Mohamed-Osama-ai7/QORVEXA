"""Simulation stage: verified plan -> Digital Twin orders -> simulation -> operational evaluation.

Reuses the Digital Twin public API only (``import_plan``, ``apply_scenario``, ``simulate``,
``compile_model``, ``summarize``). Simulated values stay simulated: a run that did not complete
(timeout, cancellation, event limit) yields no evidence; deadlocks and unfinished plans are
operational findings, not successes.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction

from qorvexa.industrial.co_orchestration.contracts import (
    CandidateStatus,
    Contradiction,
    ExperimentConfig,
    ResourceBinding,
    SimEvidence,
    Violation,
)
from qorvexa.industrial.decision_intelligence.models import Admitted
from qorvexa.industrial.digital_twin import (
    DistKind,
    FactoryModel,
    PlanImportError,
    ReportStatus,
    Scenario,
    apply_scenario,
    import_plan,
    model_sha256,
    simulate,
    summarize,
)
from qorvexa.industrial.digital_twin.kernel import CancelToken
from qorvexa.industrial.digital_twin.validation import compile_model
from qorvexa.industrial.production_planning import PlanningProblem
from qorvexa.industrial.units import Quantity, UnitError, parse_unit

_INCOMPLETE = {ReportStatus.TIMEOUT, ReportStatus.CANCELLED, ReportStatus.EVENT_LIMIT}


def twin_for(base: FactoryModel, changes: tuple[object, ...], label: str) -> FactoryModel:
    if not changes:
        return base
    sc = Scenario(
        scenario_id=label[:60] or "candidate",
        baseline_model_sha256=model_sha256(base),
        changes=changes,
    )
    return apply_scenario(base, sc)


def _dec(f: Fraction | int | float | str) -> Decimal:
    if isinstance(f, Fraction):
        return Decimal(f.numerator) / Decimal(f.denominator)
    return Decimal(str(f))


def contradictions(
    problem: PlanningProblem, twin: FactoryModel, bindings: tuple[ResourceBinding, ...]
) -> tuple[Contradiction, ...]:
    """Compare planning consumption rates with twin per-piece times on the bound machines.
    Disagreement is preserved as a contradiction; nothing is reconciled automatically."""
    out: list[Contradiction] = []
    machines = {m.machine_id for m in twin.machines}
    routes = {r.routing_id: r for r in twin.routings}
    prod_route = {p.product_id: routes.get(p.routing_id) for p in twin.products}
    res = {r.id: r for r in problem.resources}
    for b in bindings:
        bound = set(b.machine_ids) & machines
        r = res.get(b.resource_id)
        if r is None:
            out.append(
                Contradiction(
                    code="UNKNOWN_RESOURCE",
                    resource_id=b.resource_id,
                    product_id=None,
                    planning_value="",
                    twin_value="",
                    detail="binding names no planning resource",
                )
            )
            continue
        if not bound:
            out.append(
                Contradiction(
                    code="NO_BOUND_MACHINE",
                    resource_id=b.resource_id,
                    product_id=None,
                    planning_value=str(r.capacity),
                    twin_value="",
                    detail="no bound machine exists in the twin",
                )
            )
            continue
        for p in problem.products:
            cons = next(
                (c for c in problem.consumptions if c.resource_id == r.id and c.product_id == p.id),
                None,
            )
            route = prod_route.get(p.id)
            twin_s: Fraction | None = Fraction(0)
            used = False
            if route is not None:
                for op in route.operations:
                    if not set(op.machines) & bound:
                        continue
                    used = True
                    if (
                        op.processing is None
                        or op.processing.kind is not DistKind.DETERMINISTIC
                        or op.processing.value is None
                    ):
                        twin_s = None
                        break
                    try:
                        q = (
                            Quantity.of(op.processing.value, op.processing.unit)
                            .to("s")
                            .result.value
                        )
                    except UnitError:
                        twin_s = None
                        break
                    twin_s = (twin_s or Fraction(0)) + q
            plan_amt = cons.amount_per_unit if cons is not None else Decimal(0)
            if cons is None and not used:
                continue
            if cons is not None and route is None:
                out.append(
                    Contradiction(
                        code="PRODUCT_MISSING_IN_TWIN",
                        resource_id=r.id,
                        product_id=p.id,
                        planning_value=str(plan_amt),
                        twin_value="",
                        detail="planned product has no twin routing",
                    )
                )
                continue
            if twin_s is None:
                out.append(
                    Contradiction(
                        code="RATE_NOT_COMPARABLE",
                        resource_id=r.id,
                        product_id=p.id,
                        planning_value=str(plan_amt),
                        twin_value="stochastic or unknown",
                        detail="twin time is not a deterministic value; comparison not possible",
                    )
                )
                continue
            try:
                unit = (
                    f"{cons.resource_unit}/{cons.product_unit}"
                    if cons is not None
                    else f"{r.unit}/piece"
                )
                u = parse_unit(unit)
                if u.dimension != parse_unit("s/piece").dimension:
                    out.append(
                        Contradiction(
                            code="RATE_NOT_COMPARABLE",
                            resource_id=r.id,
                            product_id=p.id,
                            planning_value=f"{plan_amt} {unit}",
                            twin_value=f"{twin_s} s/piece",
                            detail="planning resource is not measured in time per piece",
                        )
                    )
                    continue
                plan_s = Quantity.of(str(plan_amt), u).to("s/piece").result.value
            except UnitError as exc:
                out.append(
                    Contradiction(
                        code="RATE_NOT_COMPARABLE",
                        resource_id=r.id,
                        product_id=p.id,
                        planning_value=str(plan_amt),
                        twin_value=str(twin_s),
                        detail=str(exc),
                    )
                )
                continue
            if plan_s != twin_s:
                out.append(
                    Contradiction(
                        code="RATE_MISMATCH",
                        resource_id=r.id,
                        product_id=p.id,
                        planning_value=f"{_dec(plan_s)} s/piece",
                        twin_value=f"{_dec(twin_s)} s/piece",
                        detail="planning consumption and twin processing time disagree "
                        "on the bound machines",
                    )
                )
    return tuple(out)


def evaluate(
    admitted: Admitted,
    problem: PlanningProblem,
    twin: FactoryModel,
    cfg: ExperimentConfig,
    candidate_label: str,
    cancel: CancelToken | None,
    clock: Callable[[], str],
) -> tuple[CandidateStatus, tuple[str, ...], SimEvidence | None, tuple[Violation, ...]]:
    conv = cfg.conversion
    try:
        plan = import_plan(
            admitted.report,
            policy=conv.policy,
            batch_sizes=conv.batch_sizes,
            release_at=conv.release_at,
            due_at=conv.due_at,
        )
    except PlanImportError as exc:
        return CandidateStatus.PLAN_NOT_IMPORTABLE, (f"{exc.code}: {exc}",), None, ()
    if not plan.orders:
        return (
            CandidateStatus.PLAN_NOT_IMPORTABLE,
            ("the verified plan produces nothing to simulate",),
            None,
            (),
        )
    model = twin.model_copy(update={"orders": plan.orders})
    sc = cfg.simulation
    rep = simulate(
        model,
        seed=sc.seed,
        replications=sc.replications,
        max_events=sc.max_events,
        timeout_s=sc.timeout_s,
        cancel=cancel,
        scenario_id=candidate_label[:60] or None,
        clock=clock,
    )
    if rep.status is ReportStatus.INVALID_INPUT:
        return (
            CandidateStatus.SIMULATION_INVALID,
            tuple(f"{i.code}: {i.message}" for i in rep.issues if i.blocking),
            None,
            (),
        )
    if rep.status in _INCOMPLETE or any(
        r.status.value in {s.value for s in _INCOMPLETE} for r in rep.replications
    ):
        return (
            CandidateStatus.SIMULATION_INCOMPLETE,
            (f"simulation ended {rep.status.value}: no evidence",),
            None,
            (),
        )

    planned = sum(o.quantity for o in plan.orders)
    bound = sorted(
        {m for b in cfg.bindings for m in b.machine_ids} & {m.machine_id for m in model.machines}
    )
    stochastic = rep.kind is not None and rep.kind.value == "STOCHASTIC_ESTIMATE"

    def per_rep(fn) -> list[Decimal]:  # type: ignore[no-untyped-def]
        return [fn(r.metrics) for r in rep.replications]

    def rv(x) -> Decimal:  # type: ignore[no-untyped-def]
        return Decimal(0) if x is None else Decimal(x.num) / Decimal(x.den)

    series: dict[str, list[Decimal]] = {
        "sim_completed": per_rep(lambda m: Decimal(m.completed)),
        "sim_plan_shortfall": per_rep(lambda m: Decimal(planned - m.completed)),
        "sim_makespan_s": per_rep(lambda m: Decimal(m.window_s)),
        "sim_throughput_per_hour": per_rep(lambda m: rv(m.throughput_per_hour)),
        "sim_wip_avg": per_rep(lambda m: rv(m.wip_avg)),
        "sim_avg_lead_time_s": per_rep(lambda m: rv(m.avg_lead_time_s)),
        "sim_late_orders": per_rep(lambda m: Decimal(m.late_orders)),
    }
    for mid in bound:
        series[f"sim_util:{mid}"] = per_rep(lambda m, k=mid: rv(m.machine(k).utilization))
        series[f"sim_down_s:{mid}"] = per_rep(lambda m, k=mid: Decimal(m.machine(k).down_s))
        series[f"sim_blocked_s:{mid}"] = per_rep(lambda m, k=mid: Decimal(m.machine(k).blocked_s))
    late_share = Decimal(sum(1 for x in series["sim_late_orders"] if x > 0)) / Decimal(
        len(rep.replications)
    )
    metrics: dict[str, Decimal] = {"objective": Decimal(str(admitted.report.objective_value))}
    uncertainty: dict[str, tuple[str, str, str]] = {}
    for k, vals in series.items():
        if stochastic and len(vals) > 1:
            s = summarize(k, [float(v) for v in vals], stochastic=True)
            metrics[k] = (sum(vals, Decimal(0)) / Decimal(len(vals))).quantize(Decimal("0.000001"))
            lo, hi = (f"{s.ci95[0]:.6f}", f"{s.ci95[1]:.6f}") if s.ci95 else ("", "")
            uncertainty[k] = (f"{s.mean:.6f}", lo, hi)
        else:
            metrics[k] = vals[0].quantize(Decimal("0.000001"))
    if stochastic:
        metrics["sim_late_probability"] = late_share.quantize(Decimal("0.000001"))
    m0 = rep.metrics
    ev = SimEvidence(
        simulation_id=rep.simulation_id,
        report_sha256=rep.report_sha256,
        content_sha256=rep.content_sha256,
        kind=rep.kind.value if rep.kind else "",
        status=rep.status.value,
        seed=sc.seed,
        replications=len(rep.replications),
        planned_pieces=planned,
        plan_conversion=tuple(
            (ln.product_id, ln.solver_quantity, ln.simulated_quantity, ln.difference)
            for ln in plan.lines
        ),
        metrics=metrics,
        uncertainty=uncertainty,
        bottlenecks=tuple(b.machine_id for b in m0.bottlenecks),
        assumptions=rep.assumptions,
        limitations=rep.limitations,
    )

    req = cfg.requirements
    vio: list[Violation] = []
    if rep.status is ReportStatus.DEADLOCK:
        vio.append(Violation(code="DEADLOCK", detail="the simulated line deadlocks with this plan"))
    if req.require_complete_run and rep.status is ReportStatus.HORIZON_REACHED:
        vio.append(
            Violation(
                code="INCOMPLETE_WITHIN_HORIZON",
                detail="work remained when the simulation horizon ended",
            )
        )
    if req.require_plan_achieved:
        worst = max(series["sim_plan_shortfall"])
        if worst > 0:
            vio.append(
                Violation(
                    code="PLAN_NOT_ACHIEVED",
                    detail="simulated output is below the verified plan",
                    value=str(planned - int(worst)),
                    limit=str(planned),
                )
            )
    if req.max_late_orders is not None:
        lv = metrics["sim_late_orders"]
        if lv > req.max_late_orders:
            vio.append(
                Violation(
                    code="LATE_ORDERS",
                    detail="late orders exceed the stated limit",
                    value=str(lv),
                    limit=str(req.max_late_orders),
                )
            )
    if req.max_makespan_s is not None and metrics["sim_makespan_s"] > req.max_makespan_s:
        vio.append(
            Violation(
                code="MAKESPAN",
                detail="makespan exceeds the stated limit",
                value=str(metrics["sim_makespan_s"]),
                limit=str(req.max_makespan_s),
            )
        )
    if req.max_utilization is not None:
        for mid in bound:
            u = metrics[f"sim_util:{mid}"]
            if u > req.max_utilization:
                vio.append(
                    Violation(
                        code="UTILIZATION",
                        detail=f"{mid} utilization exceeds the stated limit",
                        value=str(u),
                        limit=str(req.max_utilization),
                    )
                )
    if req.max_late_probability is not None:
        if not stochastic:
            vio.append(
                Violation(
                    code="LATE_PROBABILITY_NOT_ESTIMABLE",
                    detail="a deterministic run cannot estimate a probability",
                )
            )
        elif late_share > req.max_late_probability:
            vio.append(
                Violation(
                    code="LATE_PROBABILITY",
                    detail="share of replications with lateness exceeds the limit",
                    value=str(late_share),
                    limit=str(req.max_late_probability),
                )
            )
    status = (
        CandidateStatus.OPERATIONAL_VIOLATIONS if vio else CandidateStatus.OPERATIONALLY_FEASIBLE
    )
    return status, (), ev, tuple(vio)


def productive_seconds(twin: FactoryModel, machine_ids: tuple[str, ...], until: int) -> int | None:
    """Scheduled productive time of the given machines in [0, until): shift windows minus breaks,
    planned downtime and explicit breakdowns. Computed from the calendar, not from a run."""
    cm, _ = compile_model(twin)
    if cm is None:
        return None
    total = 0
    for mid in machine_ids:
        mc = cm.machines.get(mid)
        if mc is None:
            continue
        cuts = [*mc.planned, *((a, a + d) for m, a, d in cm.breakdowns if m == mid)]
        for s, e in cm.calendars.get(mc.calendar_id, ()):
            s, e = max(0, s), min(e, until)
            if e <= s:
                continue
            seg = [(s, e)]
            for a, b in cuts:
                nxt = []
                for x, y in seg:
                    if b <= x or a >= y:
                        nxt.append((x, y))
                        continue
                    if x < a:
                        nxt.append((x, a))
                    if b < y:
                        nxt.append((b, y))
                seg = nxt
            total += sum(y - x for x, y in seg)
    return total
