"""Production report builder over *trusted* runtime results.

Only a ``SolverRuntimeResult`` that passes every gate below can yield a recommendation:
  SOLVED, tier GAP_NUMERICAL (never presented as exact optimality), lowering evidence bound to the
  caller-supplied model hash, a VERIFIED primal-feasibility claim, and primal values for exactly the
  manifest's variables. Anything else yields ``NO_VERIFIED_RECOMMENDATION`` with the reason; raw
  candidates are never promoted. Utilization/remaining capacity/demand fulfilment are recomputed
  here from the manifest's business coefficients and the verified primal values (no solver output
  other than the primal vector is used). "At capacity" means within tolerance on that primal
  point; it is not a shadow-price statement (the runtime result carries no verified duals).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

from qorvexa.contracts.solver_runtime import (
    MathOutcome,
    OptimalityTier,
    SolverRuntimeResult,
    VerificationClaimKind,
    VerificationLevel,
    VerificationStatus,
)
from qorvexa.industrial.production_planning.compiler import FormulationManifest
from qorvexa.industrial.production_planning.model import ObjectiveMode

REPORT_TOLERANCE: Final = Decimal("1e-6")
_F = ConfigDict(frozen=True, extra="forbid")


class ReportStatus(StrEnum):
    VERIFIED_NUMERICAL_SOLUTION = "VERIFIED_NUMERICAL_SOLUTION"
    INFEASIBLE_VERIFIED = "INFEASIBLE_VERIFIED"
    UNBOUNDED_VERIFIED = "UNBOUNDED_VERIFIED"
    NO_VERIFIED_RECOMMENDATION = "NO_VERIFIED_RECOMMENDATION"


class ProductQuantity(BaseModel):
    model_config = _F
    product_id: str
    variable_id: str
    unit: str
    quantity: Decimal


class ResourceUtilization(BaseModel):
    model_config = _F
    resource_id: str
    unit: str
    capacity: Decimal
    used: Decimal
    remaining: Decimal
    utilization: Decimal | None  # None when capacity is 0
    at_capacity: bool


class DemandFulfillment(BaseModel):
    model_config = _F
    product_id: str
    quantity: Decimal
    minimum: Decimal
    maximum: Decimal | None
    meets_minimum: bool
    at_maximum: bool


class ProductionReport(BaseModel):
    model_config = _F
    status: ReportStatus
    reason: str
    problem_id: str
    mode: ObjectiveMode
    quantities: tuple[ProductQuantity, ...] = ()
    objective_value: Decimal | None = None
    objective_currency: str | None = None
    objective_matches_runtime: bool | None = None
    utilization: tuple[ResourceUtilization, ...] = ()
    demand: tuple[DemandFulfillment, ...] = ()
    bottleneck_resource_ids: tuple[str, ...] = ()
    epistemic_note: str = ""


_NOTE: Final = (
    "Numerical gap verification only (GAP_NUMERICAL): not an exact optimality proof. "
    "Bottlenecks are resources at capacity on this primal point, not shadow prices."
)


def _none(manifest: FormulationManifest, reason: str) -> ProductionReport:
    return ProductionReport(
        status=ReportStatus.NO_VERIFIED_RECOMMENDATION,
        reason=reason,
        problem_id=manifest.problem_id,
        mode=manifest.mode,
    )


def _claim_ok(
    result: SolverRuntimeResult, kind: VerificationClaimKind, level: VerificationLevel | None = None
) -> bool:
    return any(
        c.claim_kind == kind
        and c.status == VerificationStatus.VERIFIED
        and (level is None or c.level == level)
        for c in result.claims
    )


def build_production_report(
    manifest: FormulationManifest, result: SolverRuntimeResult, *, expected_model_sha256: str
) -> ProductionReport:
    low = result.lowering_evidence
    if low is None or low.model_sha256 != expected_model_sha256:
        return _none(manifest, "result is not bound to the expected canonical model hash")

    if result.outcome == MathOutcome.INFEASIBLE:
        ev = result.infeasibility_evidence
        if (
            ev is not None
            and ev.model_sha256 == expected_model_sha256
            and _claim_ok(
                result, VerificationClaimKind.INFEASIBILITY, VerificationLevel.EXACT_RATIONAL
            )
        ):
            return ProductionReport(
                status=ReportStatus.INFEASIBLE_VERIFIED,
                reason="exact Farkas certificate: no production plan satisfies all constraints",
                problem_id=manifest.problem_id,
                mode=manifest.mode,
            )
        return _none(manifest, "infeasibility claim lacks bound exact evidence")
    if result.outcome == MathOutcome.UNBOUNDED:
        ev2 = result.unbounded_evidence
        if (
            ev2 is not None
            and ev2.model_sha256 == expected_model_sha256
            and _claim_ok(
                result, VerificationClaimKind.UNBOUNDEDNESS, VerificationLevel.EXACT_RATIONAL
            )
        ):
            return ProductionReport(
                status=ReportStatus.UNBOUNDED_VERIFIED,
                reason="exact unbounded ray: objective is unbounded; add capacity or demand limits",
                problem_id=manifest.problem_id,
                mode=manifest.mode,
            )
        return _none(manifest, "unboundedness claim lacks bound exact evidence")
    if (
        result.outcome != MathOutcome.SOLVED
        or result.optimality_tier != OptimalityTier.GAP_NUMERICAL
    ):
        return _none(manifest, f"outcome {result.outcome.value} carries no verified solution")
    if not _claim_ok(result, VerificationClaimKind.PRIMAL_FEASIBILITY):
        return _none(manifest, "primal feasibility is not verified")
    primal = result.primal_solution
    ids = {v.variable_id for v in manifest.variables}
    if primal is None or set(primal) != ids:
        return _none(manifest, "primal solution does not match the formulation's variables")

    qty = {v.variable_id: Decimal(repr(float(primal[v.variable_id]))) for v in manifest.variables}
    coef_obj = {t.variable_id: t.coefficient for t in manifest.objective.terms}
    objective = sum((coef_obj[k] * qty[k] for k in qty), Decimal(0)) + manifest.objective.constant

    quantities = tuple(
        ProductQuantity(
            product_id=v.product_id,
            variable_id=v.variable_id,
            unit=v.unit,
            quantity=qty[v.variable_id],
        )
        for v in manifest.variables
    )
    utils: list[ResourceUtilization] = []
    for c in manifest.constraints:
        used = sum((t.coefficient * qty[t.variable_id] for t in c.terms), Decimal(0))
        tol = REPORT_TOLERANCE * max(Decimal(1), c.capacity)
        utils.append(
            ResourceUtilization(
                resource_id=c.resource_id,
                unit=c.unit,
                capacity=c.capacity,
                used=used,
                remaining=c.capacity - used,
                utilization=None if c.capacity == 0 else used / c.capacity,
                at_capacity=abs(c.capacity - used) <= tol,
            )
        )
    dem: list[DemandFulfillment] = []
    for v in manifest.variables:
        q = qty[v.variable_id]
        tol = REPORT_TOLERANCE * max(Decimal(1), abs(v.lower))
        dem.append(
            DemandFulfillment(
                product_id=v.product_id,
                quantity=q,
                minimum=v.lower,
                maximum=v.upper,
                meets_minimum=q >= v.lower - tol,
                at_maximum=v.upper is not None
                and abs(v.upper - q) <= REPORT_TOLERANCE * max(Decimal(1), v.upper),
            )
        )
    runtime_obj = result.objective_value
    matches = None
    if runtime_obj is not None:
        matches = abs(Decimal(repr(runtime_obj)) - objective) <= REPORT_TOLERANCE * max(
            Decimal(1), abs(objective)
        )
    return ProductionReport(
        status=ReportStatus.VERIFIED_NUMERICAL_SOLUTION,
        reason="solver result passed independent exact primal/dual numerical-gap verification",
        problem_id=manifest.problem_id,
        mode=manifest.mode,
        quantities=quantities,
        objective_value=objective,
        objective_currency=manifest.objective.currency,
        objective_matches_runtime=matches,
        utilization=tuple(utils),
        demand=tuple(dem),
        bottleneck_resource_ids=tuple(u.resource_id for u in utils if u.at_capacity),
        epistemic_note=_NOTE,
    )
