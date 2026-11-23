"""Industrial units engine: exact dimensions, quantities, conversion, dimensional validation."""

from qorvexa.industrial.units.dimension import Dimension, UnitError
from qorvexa.industrial.units.parser import Unit, parse_unit
from qorvexa.industrial.units.planning import (
    PlanConsumption,
    PlanningUnitsInput,
    PlanProduct,
    PlanResource,
    planning_units_from_problem,
    validate_planning_units,
)
from qorvexa.industrial.units.quantity import (
    Compat,
    ConversionResult,
    Quantity,
    classify,
    exact_number,
)
from qorvexa.industrial.units.registry import UnitDef, UnitRegistry
from qorvexa.industrial.units.validator import (
    BoundUnits,
    ConstraintUnits,
    ObjectiveUnits,
    Term,
    UnitIssue,
    UnitReport,
    UnitStatus,
    validate_linear_units,
)

__all__ = [
    "BoundUnits",
    "Compat",
    "ConstraintUnits",
    "ConversionResult",
    "Dimension",
    "ObjectiveUnits",
    "PlanConsumption",
    "PlanProduct",
    "PlanResource",
    "PlanningUnitsInput",
    "Quantity",
    "Term",
    "Unit",
    "UnitDef",
    "UnitError",
    "UnitIssue",
    "UnitRegistry",
    "UnitReport",
    "UnitStatus",
    "classify",
    "exact_number",
    "parse_unit",
    "planning_units_from_problem",
    "validate_linear_units",
    "validate_planning_units",
]
