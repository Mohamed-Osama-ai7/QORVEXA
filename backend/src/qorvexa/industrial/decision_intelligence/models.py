"""Shared decision-intelligence types (immutable, JSON-able)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from qorvexa.contracts.solver_runtime import SolverRuntimeResult
from qorvexa.industrial.production_planning.admission import AdmissionPolicy, AdmissionRecord
from qorvexa.industrial.production_planning.compiler import CompiledFormulation
from qorvexa.industrial.production_planning.model import Exact, PlanningProblem
from qorvexa.industrial.production_planning.report import ProductionReport
from qorvexa.industrial.units.evidence import LEGACY, UnitEvidence

F = ConfigDict(frozen=True, extra="forbid")


class DecisionInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class MetricDirection(StrEnum):
    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


@dataclass(frozen=True)
class ScenarioRun:
    """A formulated scenario plus whatever the real orchestrator returned for it."""

    scenario_id: str
    problem: PlanningProblem
    formulation: CompiledFormulation
    result: SolverRuntimeResult | None
    model_sha256: str | None  # OrchestrationOutcome.model_sha256 (re-verified by the engine)
    units: UnitEvidence | None = None  # None = legacy model, dimensions NOT verified
    admission: AdmissionRecord | None = None  # None = not admitted by the gate


@dataclass(frozen=True)
class Admitted:
    run: ScenarioRun
    report: ProductionReport
    metrics: dict[str, Decimal]
    model_sha256: str
    input_sha256: str
    units_status: str = LEGACY
    unit_report_sha256: str = ""
    admission_status: str = "NOT_ADMITTED"
    admission_sha256: str = ""

    @property
    def solved(self) -> bool:
        return self.report.status.value == "VERIFIED_NUMERICAL_SOLUTION"


class Rejection(BaseModel):
    model_config = F
    scenario_id: str
    code: str
    reason: str


def _metric_key(v: object) -> str:
    if not isinstance(v, str) or not v or len(v) > 80:
        raise ValueError("metric key must be a non-empty string")
    return v


MetricKey = Annotated[str, BeforeValidator(_metric_key)]


class MetricObjective(BaseModel):
    model_config = F
    metric: MetricKey
    direction: MetricDirection


class Preference(BaseModel):
    model_config = F
    metric: MetricKey
    direction: MetricDirection
    weight: Exact


class HardConstraint(BaseModel):
    model_config = F
    metric: MetricKey
    op: Literal["<=", ">="]
    threshold: Exact


class RankingMode(StrEnum):
    OBJECTIVE_FIRST = "OBJECTIVE_FIRST"
    CONSTRAINT_FIRST = "CONSTRAINT_FIRST"
    WEIGHTED_PREFERENCE = "WEIGHTED_PREFERENCE"


class DecisionConfig(BaseModel):
    """Everything the user must state explicitly; nothing is defaulted on their behalf."""

    model_config = F
    ranking_mode: RankingMode = RankingMode.OBJECTIVE_FIRST
    hard_constraints: tuple[HardConstraint, ...] = ()
    preferences: tuple[Preference, ...] = ()
    pareto_objectives: tuple[MetricObjective, ...] = ()
    acknowledged_assumptions: frozenset[str] = Field(default_factory=frozenset)
    require_unit_evidence: bool = False  # compat alias: True == STRICT
    admission_policy: AdmissionPolicy = AdmissionPolicy.LEGACY_UNVERIFIED

    @property
    def strict(self) -> bool:
        return self.require_unit_evidence or self.admission_policy is AdmissionPolicy.STRICT
