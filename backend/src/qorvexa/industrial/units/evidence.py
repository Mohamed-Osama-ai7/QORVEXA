"""Self-contained, recomputable unit evidence carried to the solver gate and decision briefs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.units.custom import CustomUnitDef, build_registry, parse_factor
from qorvexa.industrial.units.dimension import UnitError
from qorvexa.industrial.units.planning import PlanningUnitsInput, validate_planning_units
from qorvexa.industrial.units.registry import UnitRegistry
from qorvexa.industrial.units.validator import UnitReport, UnitStatus

VALIDATED = "DIMENSIONALLY_VALIDATED"
LEGACY = "LEGACY_UNITS_UNVERIFIED"


class UnitEvidence(BaseModel):
    """Everything needed to recompute the unit report: nothing here is trusted, all re-derived."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    inputs: PlanningUnitsInput
    custom_units: tuple[tuple[str, str, str], ...] = ()  # (symbol, expression, exact factor)
    report_sha256: str


def registry_of(ev: UnitEvidence) -> UnitRegistry:
    defs = [CustomUnitDef(s, e, parse_factor(f)) for s, e, f in ev.custom_units]
    reg, errors = build_registry(defs)
    if errors:
        first = sorted(errors)[0]
        raise UnitError(errors[first].code, f"custom unit {first!r}: {errors[first].message}")
    return reg


def recompute_report(ev: UnitEvidence) -> UnitReport:
    return validate_planning_units(ev.inputs, registry_of(ev))


def verify_evidence(ev: UnitEvidence) -> UnitReport:
    """Recompute from scratch; raise UnitError unless CONSISTENT and the hash matches."""
    report = recompute_report(ev)
    if report.status is not UnitStatus.CONSISTENT:
        raise UnitError("UNIT_VALIDATION_FAILED", f"unit report is {report.status.value}")
    if report.sha256 != ev.report_sha256:
        raise UnitError("UNIT_EVIDENCE_MISMATCH", "unit report hash does not match recomputation")
    return report
