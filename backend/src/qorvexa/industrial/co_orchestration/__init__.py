"""Optimization-Simulation Co-Orchestration (OSCO).

Production Planning (STRICT) -> Solver Fabric (real solver) -> independent verification ->
Digital Twin simulation -> operational evaluation -> justified refinement -> Decision Intelligence
Pareto/ranking -> governed recommendation package (always awaiting human approval).
"""

from qorvexa.industrial.co_orchestration.contracts import (
    ENGINE_VERSION,
    OSCO_SCHEMA,
    Budget,
    Candidate,
    CandidateOrigin,
    CandidateSpec,
    CandidateStatus,
    Contradiction,
    DecisionPackage,
    DecisionSpec,
    ExperimentConfig,
    MathEvidence,
    OperationalRequirements,
    PlanConversion,
    Recommendation,
    RecommendationStatus,
    ResourceBinding,
    SensitivitySpec,
    SimEvidence,
    SimulationConfig,
    StopReason,
    Violation,
)
from qorvexa.industrial.co_orchestration.engine import CoOrchestrator, experiment_id
from qorvexa.industrial.co_orchestration.evaluate import contradictions, productive_seconds
from qorvexa.industrial.co_orchestration.optimize import problem_sha256, solve_and_verify
from qorvexa.industrial.co_orchestration.store import (
    InMemoryExperimentStore,
    OscoStoreError,
    SqliteExperimentStore,
    package_sha256,
    verify_package,
)

__all__ = [
    "ENGINE_VERSION",
    "OSCO_SCHEMA",
    "Budget",
    "Candidate",
    "CandidateOrigin",
    "CandidateSpec",
    "CandidateStatus",
    "CoOrchestrator",
    "Contradiction",
    "DecisionPackage",
    "DecisionSpec",
    "ExperimentConfig",
    "InMemoryExperimentStore",
    "MathEvidence",
    "OperationalRequirements",
    "OscoStoreError",
    "PlanConversion",
    "Recommendation",
    "RecommendationStatus",
    "ResourceBinding",
    "SensitivitySpec",
    "SimEvidence",
    "SimulationConfig",
    "SqliteExperimentStore",
    "StopReason",
    "Violation",
    "contradictions",
    "experiment_id",
    "package_sha256",
    "problem_sha256",
    "productive_seconds",
    "solve_and_verify",
    "verify_package",
]
