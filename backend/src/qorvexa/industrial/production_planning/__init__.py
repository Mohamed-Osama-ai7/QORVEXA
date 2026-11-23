"""Production planning (continuous LP): domain model, deterministic compiler, scenarios, reports.

Formulation layer only. It produces strict-ingress JSON for the Solver Fabric and never solves.
"""

from qorvexa.industrial.production_planning.compiler import (
    CompiledFormulation,
    FormulationManifest,
    compile_problem,
)
from qorvexa.industrial.production_planning.model import (
    Consumption,
    ObjectiveMode,
    PlanningInputError,
    PlanningMetadata,
    PlanningProblem,
    Product,
    Resource,
    ResourceKind,
    parse_planning_json,
    validate_planning_dict,
)
from qorvexa.industrial.production_planning.report import (
    ProductionReport,
    ReportStatus,
    build_production_report,
)
from qorvexa.industrial.production_planning.scenario import (
    CapacityChange,
    CompiledScenario,
    DemandBoundsChange,
    ScenarioSpec,
    ValueChange,
    compile_scenario,
    parse_scenario,
)

__all__ = [
    "CapacityChange",
    "CompiledFormulation",
    "CompiledScenario",
    "Consumption",
    "DemandBoundsChange",
    "FormulationManifest",
    "ObjectiveMode",
    "PlanningInputError",
    "PlanningMetadata",
    "PlanningProblem",
    "Product",
    "ProductionReport",
    "ReportStatus",
    "Resource",
    "ResourceKind",
    "ScenarioSpec",
    "ValueChange",
    "build_production_report",
    "compile_problem",
    "compile_scenario",
    "parse_planning_json",
    "parse_scenario",
    "validate_planning_dict",
]
