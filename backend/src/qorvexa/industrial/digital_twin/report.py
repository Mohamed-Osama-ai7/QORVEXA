"""Run orchestration and the versioned, hash-bound simulation report."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import (
    ENGINE_VERSION,
    MODEL_SCHEMA,
    FactoryModel,
    SetupPolicy,
    SourceRef,
)
from qorvexa.industrial.digital_twin.kernel import (
    DEFAULT_MAX_EVENTS,
    CancelToken,
    RunStatus,
    Simulator,
)
from qorvexa.industrial.digital_twin.metrics import Metrics, compute_metrics
from qorvexa.industrial.digital_twin.stochastic import (
    MAX_REPLICATIONS,
    SAMPLE_ROUNDING,
    Summary,
    summarize,
)
from qorvexa.industrial.digital_twin.validation import Issue, UnitEvidence, compile_model

F = ConfigDict(frozen=True, extra="forbid")
REPORT_SCHEMA: Final = "qorvexa-twin-report/1"
DISCLAIMER: Final = (
    "Advisory simulation evidence of a model, not a factory measurement and not an optimisation "
    "proof. Valid only for the stated inputs, policies and assumptions."
)


class RunKind(StrEnum):
    DETERMINISTIC_SIMULATION = "DETERMINISTIC_SIMULATION"
    STOCHASTIC_ESTIMATE = "STOCHASTIC_ESTIMATE"


class ReportStatus(StrEnum):
    COMPLETED = "COMPLETED"
    HORIZON_REACHED = "HORIZON_REACHED"
    DEADLOCK = "DEADLOCK"
    EVENT_LIMIT = "EVENT_LIMIT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INVALID_INPUT = "INVALID_INPUT"


class Parameters(BaseModel):
    model_config = F
    seed: int
    replications: int
    max_events: int
    timeout_s: float
    trace_limit: int


class Replication(BaseModel):
    model_config = F
    replication: int
    status: RunStatus
    events: int
    event_digest: str
    end_time_s: int
    deadlock_machines: tuple[str, ...]
    trace_sample: tuple[tuple[int, str, str, str], ...]
    trace_truncated: bool
    metrics: Metrics


class SimulationReport(BaseModel):
    model_config = F
    report_schema: str = REPORT_SCHEMA
    engine_version: str = ENGINE_VERSION
    model_schema: str = MODEL_SCHEMA
    simulation_id: str
    factory_id: str
    model_id: str
    model_sha256: str
    scenario_id: str | None = None
    scenario_sha256: str | None = None
    baseline_model_sha256: str | None = None
    sources: tuple[SourceRef, ...]
    unit_evidence: UnitEvidence | None
    issues: tuple[Issue, ...]
    parameters: Parameters
    assumptions: tuple[str, ...]
    kind: RunKind | None
    status: ReportStatus
    replications: tuple[Replication, ...]
    summaries: tuple[Summary, ...]
    limitations: tuple[str, ...]
    disclaimer: str = DISCLAIMER
    created_at: str
    content_sha256: str = ""
    report_sha256: str = ""

    @property
    def metrics(self) -> Metrics:
        """Metrics of replication 0 (the deterministic result, or the first stochastic sample)."""
        return self.replications[0].metrics


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def content_sha256(r: SimulationReport) -> str:
    return _sha(
        r.model_dump(mode="json", exclude={"created_at", "content_sha256", "report_sha256"})
    )


def report_sha256(r: SimulationReport) -> str:
    return _sha(r.model_dump(mode="json", exclude={"report_sha256"}))


def verify_report(r: SimulationReport) -> bool:
    return r.content_sha256 == content_sha256(r) and r.report_sha256 == report_sha256(r)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _assumptions(model: FactoryModel, stochastic: bool) -> tuple[str, ...]:
    p = model.policies
    out = [
        "processing time of a job = per-piece time x pieces in the transfer batch",
        f"dispatch policy {p.dispatch.value}; no preemption between jobs",
        f"interrupted setups/operations: {p.interrupt.value}",
        "materials are consumed when an operation starts",
        "shared resource units are held during interruptions",
        "unplanned repairs proceed in wall-clock time, also outside shifts",
        "blocking after service: a finished job waits on its machine for downstream space",
    ]
    if p.blocked_machine_may_unload_off_shift:
        out.append("a blocked machine may hand over its finished job while off shift")
    if any(
        op.setup_policy is SetupPolicy.ON_PRODUCT_CHANGE
        for r in model.routings
        for op in r.operations
    ):
        out.append("ON_PRODUCT_CHANGE setup also applies to the first job a machine runs")
    if stochastic:
        out.append(f"sampled durations are converted to ticks by {SAMPLE_ROUNDING}")
        out.append(
            "stochastic scrap is sampled per piece (Bernoulli); "
            "deterministic runs accumulate exactly"
        )
        out.append("one processing sample per job, scaled by the pieces in the batch")
    return (*out, *model.assumptions)


def simulate(
    model: FactoryModel,
    *,
    seed: int = 0,
    replications: int = 1,
    scenario_id: str | None = None,
    scenario_sha256: str | None = None,
    baseline_model_sha256: str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    timeout_s: float = 60.0,
    cancel: CancelToken | None = None,
    trace_limit: int = 20_000,
    clock: Callable[[], str] = _now,
) -> SimulationReport:
    """Validate, compile and run ``replications`` independent replications."""
    if not (1 <= replications <= MAX_REPLICATIONS):
        raise ValueError(f"INVALID_REPLICATIONS: 1..{MAX_REPLICATIONS}")
    if not (1 <= max_events <= 50_000_000) or not (0 < timeout_s <= 3600):
        raise ValueError("INVALID_BUDGET: max_events 1..5e7 and timeout 0..3600 s")
    cm, vrep = compile_model(model)
    params = Parameters(
        seed=seed, replications=replications, max_events=max_events, timeout_s=timeout_s,
        trace_limit=trace_limit,
    )  # fmt: skip
    sim_id = "sim-" + _sha([vrep.model_sha256, scenario_sha256, seed, replications])[:20]
    base = {
        "simulation_id": sim_id, "factory_id": model.factory_id, "model_id": model.model_id,
        "model_sha256": vrep.model_sha256, "scenario_id": scenario_id,
        "scenario_sha256": scenario_sha256, "baseline_model_sha256": baseline_model_sha256,
        "sources": model.sources, "unit_evidence": vrep.unit_evidence, "issues": vrep.issues,
        "parameters": params, "created_at": clock(),
    }  # fmt: skip
    if cm is None:
        rep = SimulationReport(
            **base,
            assumptions=model.assumptions,
            kind=None,
            status=ReportStatus.INVALID_INPUT,
            replications=(),
            summaries=(),
            limitations=("Not simulated: the model has blocking input issues.",),
        )
        return _seal(rep)
    reps: list[Replication] = []
    for r in range(replications):
        run = Simulator(
            cm, seed=seed, replication=r, max_events=max_events, timeout_s=timeout_s,
            cancel=cancel, trace_limit=trace_limit,
        ).run()  # fmt: skip
        reps.append(
            Replication(
                replication=r, status=run.status, events=run.events, event_digest=run.event_digest,
                end_time_s=run.end_time, deadlock_machines=run.deadlock,
                trace_sample=tuple(run.trace[:500]),
                trace_truncated=run.trace_truncated or len(run.trace) > 500,
                metrics=compute_metrics(run),
            )
        )  # fmt: skip
        if run.status in (RunStatus.CANCELLED, RunStatus.TIMEOUT):
            break
    kind = RunKind.STOCHASTIC_ESTIMATE if cm.stochastic else RunKind.DETERMINISTIC_SIMULATION
    statuses = [x.status for x in reps]
    worst = next(
        (
            s
            for s in (
                RunStatus.CANCELLED,
                RunStatus.TIMEOUT,
                RunStatus.EVENT_LIMIT,
                RunStatus.DEADLOCK,
                RunStatus.HORIZON_REACHED,
            )
            if s in statuses
        ),
        RunStatus.COMPLETED,
    )
    summaries: list[Summary] = []
    if len(reps) > 1:

        def vals(fn: Callable[[Metrics], object]) -> list[float]:
            out = []
            for x in reps:
                v = fn(x.metrics)
                out.append(float(v.value) if hasattr(v, "value") else float(v or 0))  # type: ignore[arg-type]
            return out

        st = cm.stochastic
        summaries = [
            summarize("completed", vals(lambda m: m.completed), stochastic=st),
            summarize("throughput_per_hour", vals(lambda m: m.throughput_per_hour), stochastic=st),
            summarize("wip_avg", vals(lambda m: m.wip_avg), stochastic=st),
            summarize("avg_lead_time_s", vals(lambda m: m.avg_lead_time_s), stochastic=st),
            summarize("late_orders", vals(lambda m: m.late_orders), stochastic=st),
        ]
        for mid in sorted(cm.machines):
            summaries.append(
                summarize(
                    f"utilization:{mid}",
                    vals(lambda m, k=mid: m.machine(k).utilization),  # type: ignore[misc]
                    stochastic=st,
                )
            )
    limits = [
        "Linear routings, single-server machines, no preemption between jobs.",
        "Synthetic or supplied model data; results are not real-plant validation.",
    ]
    if worst is RunStatus.HORIZON_REACHED:
        limits.append("The horizon ended with work remaining; throughput reflects a partial run.")
    if worst is RunStatus.DEADLOCK:
        limits.append("The run stopped in a deadlock; metrics cover the time before it.")
    if any(x.trace_truncated for x in reps):
        limits.append("Event trace is sampled; the event digest covers every event.")
    if cm.stochastic:
        limits.append(
            "Stochastic estimates depend on the explicitly configured distributions only."
        )
    rep = SimulationReport(
        **base,
        assumptions=_assumptions(model, cm.stochastic),
        kind=kind,
        status=ReportStatus(worst.value),
        replications=tuple(reps),
        summaries=tuple(summaries),
        limitations=tuple(limits),
    )
    return _seal(rep)


def _seal(rep: SimulationReport) -> SimulationReport:
    rep = rep.model_copy(update={"content_sha256": content_sha256(rep)})
    return rep.model_copy(update={"report_sha256": report_sha256(rep)})
