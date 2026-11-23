"""Production-plan import: verified continuous LP quantities -> discrete orders, by explicit policy.

The exact solver quantity is preserved next to the simulated quantity; nothing is rounded unless the
caller chose a conversion policy, and any difference is recorded.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.digital_twin.contracts import Order, Qty, QuantityPolicy
from qorvexa.industrial.production_planning.report import ProductionReport, ReportStatus
from qorvexa.industrial.units import UnitError, parse_unit

F = ConfigDict(frozen=True, extra="forbid")


class PlanImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class PlanLine(BaseModel):
    model_config = F
    product_id: str
    solver_quantity: str  # exact, as verified
    unit: str
    simulated_quantity: int
    difference: str  # simulated - solver
    policy: QuantityPolicy
    batch_size: int


class ImportedPlan(BaseModel):
    model_config = F
    problem_id: str
    lines: tuple[PlanLine, ...]
    orders: tuple[Order, ...]
    skipped_zero: tuple[str, ...]


def import_plan(
    report: ProductionReport,
    *,
    policy: QuantityPolicy,
    batch_sizes: dict[str, int],
    release_at: Qty,
    due_at: Qty | None = None,
    product_map: dict[str, str] | None = None,
) -> ImportedPlan:
    if report.status is not ReportStatus.VERIFIED_NUMERICAL_SOLUTION:
        raise PlanImportError("PLAN_NOT_VERIFIED", f"plan status is {report.status.value}")
    lines, orders, zeros = [], [], []
    for q in report.quantities:
        pid = (product_map or {}).get(q.product_id, q.product_id)
        try:
            u = parse_unit(q.unit)
        except UnitError as exc:
            raise PlanImportError("UNIT_REJECTED", f"{q.unit}: {exc}") from exc
        if u.dimension != parse_unit("piece").dimension:
            raise PlanImportError(
                "UNIT_MISMATCH", f"{q.product_id}: {q.unit!r} is not a count of pieces"
            )
        if u.symbol != "piece":
            raise PlanImportError(
                "UNIT_CONVERSION_REQUIRED",
                f"{q.product_id}: convert {q.unit!r} to pieces explicitly",
            )
        batch = batch_sizes.get(pid)
        if batch is None or batch < 1:
            raise PlanImportError(
                "MISSING_BATCH_SIZE", f"{pid}: batch size must be given explicitly"
            )
        exact = Decimal(q.quantity)
        if exact < 0:
            raise PlanImportError("NEGATIVE_QUANTITY", q.product_id)
        if policy is QuantityPolicy.EXACT_INTEGER_ONLY:
            if exact != exact.to_integral_value():
                raise PlanImportError(
                    "FRACTIONAL_QUANTITY",
                    f"{q.product_id}: {exact} pieces is not a whole number; "
                    "choose a conversion policy",
                )
            n = int(exact)
        else:
            whole_batches = exact / batch
            k = int(
                whole_batches.to_integral_value(
                    rounding="ROUND_FLOOR"
                    if policy is QuantityPolicy.FLOOR_TO_BATCH
                    else "ROUND_CEILING"
                )
            )
            n = k * batch
        if n == 0:
            zeros.append(pid)
            continue
        lines.append(
            PlanLine(
                product_id=pid, solver_quantity=str(exact), unit=q.unit, simulated_quantity=n,
                difference=str(Decimal(n) - exact), policy=policy, batch_size=batch,
            )
        )  # fmt: skip
        orders.append(
            Order(
                order_id=f"plan:{report.problem_id}:{pid}",
                product_id=pid,
                quantity=n,
                release_at=release_at,
                due_at=due_at,
            )
        )
    return ImportedPlan(
        problem_id=report.problem_id,
        lines=tuple(lines),
        orders=tuple(orders),
        skipped_zero=tuple(zeros),
    )
