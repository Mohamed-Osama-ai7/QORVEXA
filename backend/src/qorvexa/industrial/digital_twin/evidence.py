"""Typed simulation evidence for Decision Intelligence (independent adapter; DI is not modified)."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.report import SimulationReport, verify_report
from qorvexa.industrial.digital_twin.scenario import Comparison

F = ConfigDict(frozen=True, extra="forbid")
EVIDENCE_SCHEMA = "qorvexa-simulation-evidence/1"
NOT_PROOF = (
    "Simulation evidence: model behaviour under stated assumptions. It is not an optimisation "
    "guarantee, not a solver certificate and not a measurement of the real factory."
)


class MetricValue(BaseModel):
    model_config = F
    name: str
    value: str | None
    basis: str  # SIMULATED | ESTIMATE_MEAN


class SimulationEvidence(BaseModel):
    model_config = F
    schema_version: str = EVIDENCE_SCHEMA
    simulation_id: str
    report_sha256: str
    model_sha256: str
    scenario_sha256: str | None
    factory_id: str
    ontology_revision: int | None
    kind: str
    status: str
    seed: int
    replications: int
    metrics: tuple[MetricValue, ...]
    bottleneck_candidates: tuple[str, ...]
    comparisons: tuple[Comparison, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    statement: str = NOT_PROOF
    evidence_sha256: str = ""


def to_evidence(
    report: SimulationReport, comparisons: tuple[Comparison, ...] = ()
) -> SimulationEvidence:
    if not verify_report(report):
        raise ValueError("REPORT_TAMPERED: the report hash does not verify")
    if report.status.value == "INVALID_INPUT":
        raise ValueError("NO_EVIDENCE: the model was not simulated")
    for c in comparisons:
        if report.simulation_id not in (c.baseline_simulation_id, c.scenario_simulation_id):
            raise ValueError("UNRELATED_COMPARISON: comparison does not involve this simulation")
    m = report.metrics
    basis = (
        "SIMULATED"
        if report.kind and report.kind.value == "DETERMINISTIC_SIMULATION"
        else "ESTIMATE_MEAN"
    )
    if basis == "SIMULATED":
        vals = [
            MetricValue(name="completed", value=str(m.completed), basis=basis),
            MetricValue(
                name="throughput_per_hour",
                value=m.throughput_per_hour.value if m.throughput_per_hour else None,
                basis=basis,
            ),
            MetricValue(name="wip_avg", value=m.wip_avg.value if m.wip_avg else None, basis=basis),
            MetricValue(
                name="avg_lead_time_s",
                value=m.avg_lead_time_s.value if m.avg_lead_time_s else None,
                basis=basis,
            ),
            MetricValue(name="late_orders", value=str(m.late_orders), basis=basis),
        ]
    else:
        vals = [
            MetricValue(name=s.metric, value=f"{s.mean:.6f}", basis=basis) for s in report.summaries
        ]
    onto = next((s.revision for s in report.sources if s.kind == "ontology"), None)
    ev = SimulationEvidence(
        simulation_id=report.simulation_id,
        report_sha256=report.report_sha256,
        model_sha256=report.model_sha256,
        scenario_sha256=report.scenario_sha256,
        factory_id=report.factory_id,
        ontology_revision=onto,
        kind=str(report.kind.value if report.kind else ""),
        status=report.status.value,
        seed=report.parameters.seed,
        replications=report.parameters.replications,
        metrics=tuple(vals),
        bottleneck_candidates=tuple(b.machine_id for b in m.bottlenecks),
        comparisons=comparisons,
        assumptions=report.assumptions,
        limitations=report.limitations,
    )
    body = ev.model_dump(mode="json", exclude={"evidence_sha256"})
    return ev.model_copy(
        update={
            "evidence_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        }
    )
