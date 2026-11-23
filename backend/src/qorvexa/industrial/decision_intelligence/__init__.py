"""Decision Intelligence for production planning: compare, rank and recommend from verified runs.

Consumes only trusted Production Planning / Solver Fabric outputs; never solves or verifies itself.
"""

from qorvexa.industrial.decision_intelligence.explanation import DecisionBrief, brief_json
from qorvexa.industrial.decision_intelligence.models import (
    DecisionConfig,
    DecisionInputError,
    HardConstraint,
    MetricDirection,
    MetricObjective,
    Preference,
    RankingMode,
    ScenarioRun,
)
from qorvexa.industrial.decision_intelligence.recommendation import (
    Recommendation,
    RecommendationStatus,
)
from qorvexa.industrial.decision_intelligence.service import (
    decide,
    run_from_outcome,
    solve_run,
)

__all__ = [
    "DecisionBrief",
    "DecisionConfig",
    "DecisionInputError",
    "HardConstraint",
    "MetricDirection",
    "MetricObjective",
    "Preference",
    "RankingMode",
    "Recommendation",
    "RecommendationStatus",
    "ScenarioRun",
    "brief_json",
    "decide",
    "run_from_outcome",
    "solve_run",
]
