"""Typed, versioned contracts of the Optimization-Simulation Co-Orchestration engine (OSCO).

Nothing here has a business default: objectives, operational requirements, ranking mode, Pareto
metrics and budgets are stated by the caller. Candidate identities are content hashes.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from qorvexa.industrial.decision_intelligence.models import (
    HardConstraint,
    MetricObjective,
    Preference,
    RankingMode,
)
from qorvexa.industrial.decision_intelligence.pareto import ParetoResult
from qorvexa.industrial.decision_intelligence.ranking import RankingResult
from qorvexa.industrial.digital_twin.contracts import Change as TwinChange
from qorvexa.industrial.digital_twin.contracts import Qty, QuantityPolicy
from qorvexa.industrial.production_planning.scenario import ScenarioSpec

F = ConfigDict(frozen=True, extra="forbid")
OSCO_SCHEMA: Final = "qorvexa-osco/1"
ENGINE_VERSION: Final = "qorvexa-osco/1.0"
MAX_CANDIDATES: Final = 64
MAX_ITERATIONS: Final = 10
MAX_WORKERS: Final = 4


class CandidateOrigin(StrEnum):
    BASELINE = "BASELINE"
    PORTFOLIO = "PORTFOLIO"  # explicit scenario given by the caller
    SENSITIVITY = "SENSITIVITY"  # generated from explicit deltas
    REFINEMENT = "REFINEMENT"  # bottleneck-driven, justified by simulation evidence


class CandidateStatus(StrEnum):
    OPERATIONALLY_FEASIBLE = "OPERATIONALLY_FEASIBLE"  # verified math + complete sim + no violation
    OPERATIONAL_VIOLATIONS = "OPERATIONAL_VIOLATIONS"
    SIMULATION_INCOMPLETE = "SIMULATION_INCOMPLETE"  # timeout, cancel, deadlock, event limit
    SIMULATION_INVALID = "SIMULATION_INVALID"  # twin model rejected the inputs
    MATH_INFEASIBLE = "MATH_INFEASIBLE"  # verified infeasible: never simulated
    MATH_UNBOUNDED = "MATH_UNBOUNDED"
    NOT_VERIFIED = "NOT_VERIFIED"  # solver result failed independent verification
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    SCENARIO_INVALID = "SCENARIO_INVALID"
    PLAN_NOT_IMPORTABLE = "PLAN_NOT_IMPORTABLE"  # e.g. fractional quantities without a policy
    DUPLICATE = "DUPLICATE"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"


class ResourceBinding(BaseModel):
    """Which twin machines realise a planning resource (explicit; never inferred)."""

    model_config = F
    resource_id: str
    machine_ids: tuple[str, ...] = Field(min_length=1)


class OperationalRequirements(BaseModel):
    """Operational acceptance criteria. Every field is opt-in; none has a hidden default."""

    model_config = F
    require_complete_run: bool = True  # an incomplete simulation is never acceptable evidence
    require_plan_achieved: bool = True  # simulated completed pieces must equal planned pieces
    max_late_orders: int | None = None
    max_makespan_s: int | None = None
    max_utilization: Decimal | None = None  # per bound machine, (0, 1]
    max_late_probability: Decimal | None = None  # stochastic: share of replications with lateness


class SimulationConfig(BaseModel):
    model_config = F
    seed: int = 0
    replications: int = Field(default=1, ge=1, le=200)
    max_events: int = Field(default=2_000_000, ge=1, le=50_000_000)
    timeout_s: float = Field(default=60.0, gt=0, le=3600)


class PlanConversion(BaseModel):
    """How continuous LP quantities become discrete jobs (explicit policy, no default)."""

    model_config = F
    policy: QuantityPolicy
    batch_sizes: dict[str, int]
    release_at: Qty
    due_at: Qty | None = None


class DecisionSpec(BaseModel):
    model_config = F
    ranking_mode: RankingMode
    hard_constraints: tuple[HardConstraint, ...] = ()
    preferences: tuple[Preference, ...] = ()
    pareto_objectives: tuple[MetricObjective, ...] = ()


class Budget(BaseModel):
    model_config = F
    max_iterations: int = Field(default=3, ge=1, le=MAX_ITERATIONS)
    max_candidates: int = Field(default=12, ge=1, le=MAX_CANDIDATES)
    wall_clock_s: float = Field(default=600.0, gt=0, le=7200)
    max_workers: int = Field(default=1, ge=1, le=MAX_WORKERS)


class CandidateSpec(BaseModel):
    """A candidate = planning scenario changes + twin changes, both explicit."""

    model_config = F
    label: str = Field(min_length=1, max_length=80)
    origin: CandidateOrigin
    planning: ScenarioSpec | None = None
    twin_changes: tuple[TwinChange, ...] = ()
    parent_id: str | None = None
    justification: str = ""


class SensitivitySpec(BaseModel):
    """Explicit capacity what-ifs: one candidate per (resource, capacity) value."""

    model_config = F
    resource_id: str
    capacities: tuple[Decimal, ...] = Field(min_length=1, max_length=10)
    twin_changes: tuple[TwinChange, ...] = ()


class ExperimentConfig(BaseModel):
    model_config = F
    schema_version: str = OSCO_SCHEMA
    experiment_label: str = Field(min_length=1, max_length=120)
    bindings: tuple[ResourceBinding, ...]
    conversion: PlanConversion
    requirements: OperationalRequirements
    simulation: SimulationConfig
    decision: DecisionSpec
    budget: Budget
    portfolio: tuple[CandidateSpec, ...] = Field(default=(), max_length=MAX_CANDIDATES)
    sensitivity: tuple[SensitivitySpec, ...] = Field(default=(), max_length=10)
    refine_on_bottleneck: bool = False


# ----------------------------------------------------------------------------- results
class Violation(BaseModel):
    model_config = F
    code: str
    detail: str
    value: str | None = None
    limit: str | None = None


class Contradiction(BaseModel):
    """Planning model and twin model disagree; preserved, never auto-resolved."""

    model_config = F
    code: str
    resource_id: str
    product_id: str | None
    planning_value: str
    twin_value: str
    detail: str


class MathEvidence(BaseModel):
    model_config = F
    report_status: str
    objective: str | None
    quantities: tuple[tuple[str, str, str], ...]  # product, exact quantity, unit
    model_sha256: str
    input_sha256: str
    admission_status: str
    admission_sha256: str
    unit_report_sha256: str
    verified: bool


class SimEvidence(BaseModel):
    model_config = F
    simulation_id: str
    report_sha256: str
    content_sha256: str
    kind: str
    status: str
    seed: int
    replications: int
    planned_pieces: int
    plan_conversion: tuple[tuple[str, str, int, str], ...]  # product, solver qty, simulated, diff
    metrics: dict[str, Decimal]
    uncertainty: dict[str, tuple[str, str, str]]  # metric -> (mean, ci_low|"", ci_high|"")
    bottlenecks: tuple[str, ...]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]


class Candidate(BaseModel):
    model_config = F
    candidate_id: str
    iteration: int
    spec: CandidateSpec
    problem_sha256: str | None
    twin_model_sha256: str | None
    status: CandidateStatus
    reasons: tuple[str, ...] = ()
    math: MathEvidence | None = None
    sim: SimEvidence | None = None
    violations: tuple[Violation, ...] = ()
    contradictions: tuple[Contradiction, ...] = ()
    duplicate_of: str | None = None


class IterationRecord(BaseModel):
    model_config = F
    iteration: int
    candidate_ids: tuple[str, ...]
    new_specs: tuple[str, ...]
    note: str


class RecommendationStatus(StrEnum):
    RECOMMENDED_FOR_HUMAN_REVIEW = "RECOMMENDED_FOR_HUMAN_REVIEW"
    NO_OPERATIONALLY_FEASIBLE_CANDIDATE = "NO_OPERATIONALLY_FEASIBLE_CANDIDATE"
    BLOCKED_BY_CONTRADICTIONS = "BLOCKED_BY_CONTRADICTIONS"
    BASELINE_NOT_VERIFIED = "BASELINE_NOT_VERIFIED"
    INCOMPLETE = "INCOMPLETE"  # cancelled / budget stopped before any candidate was evaluated
    TIE_REQUIRES_HUMAN_CHOICE = "TIE_REQUIRES_HUMAN_CHOICE"


class Recommendation(BaseModel):
    model_config = F
    status: RecommendationStatus
    candidate_id: str | None
    rationale: tuple[str, ...]
    trade_offs: tuple[str, ...]
    uncertainty: tuple[str, ...]
    human_approval_required: bool = True
    approved: bool = False  # never set by this engine


class StopReason(StrEnum):
    CONVERGED = "CONVERGED"  # no new justified candidate
    MAX_ITERATIONS = "MAX_ITERATIONS"
    MAX_CANDIDATES = "MAX_CANDIDATES"
    WALL_CLOCK = "WALL_CLOCK"
    CANCELLED = "CANCELLED"
    BASELINE_FAILED = "BASELINE_FAILED"


class DecisionPackage(BaseModel):
    model_config = F
    schema_version: str = OSCO_SCHEMA
    engine_version: str = ENGINE_VERSION
    experiment_id: str
    experiment_label: str
    config_sha256: str
    baseline_problem_sha256: str
    twin_baseline_sha256: str
    baseline_candidate_id: str | None
    candidates: tuple[Candidate, ...]
    iterations: tuple[IterationRecord, ...]
    stop_reason: StopReason
    pareto: ParetoResult | None
    ranking: RankingResult | None
    recommendation: Recommendation
    limitations: tuple[str, ...]
    statement: str = (
        "Optimization results are verified mathematical solutions of the stated LP model; "
        "simulation "
        "results are modeled evidence under stated assumptions, not factory measurements. Nothing "
        "here is approved: a human decision is required."
    )
    created_at: str
    package_sha256: str = ""
