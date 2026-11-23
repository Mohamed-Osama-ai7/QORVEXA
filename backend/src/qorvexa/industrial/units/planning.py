"""Production-planning unit validation (independent typed input + read-only adapter).

Product quantities/bounds/demand, resource consumption, capacities, revenue/margin/cost
coefficients and the objective are all checked with exact dimensional algebra. The adapter
``planning_units_from_problem`` reads (never modifies) a Production Planning problem object.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from qorvexa.industrial.units.dimension import UnitError
from qorvexa.industrial.units.parser import parse_unit
from qorvexa.industrial.units.registry import UnitRegistry
from qorvexa.industrial.units.validator import (
    BoundUnits,
    ConstraintUnits,
    ObjectiveUnits,
    Term,
    UnitIssue,
    UnitReport,
    _report,
    validate_linear_units,
)


@dataclass(frozen=True)
class PlanProduct:
    id: str
    unit: str  # unit of produced quantity, e.g. "piece"
    value_unit: str  # margin / revenue / cost per unit, e.g. "EUR/piece"
    bound_unit: str | None = None  # unit of min/max (demand) quantities; default = unit


@dataclass(frozen=True)
class PlanResource:
    id: str
    capacity_unit: str  # e.g. "h"


@dataclass(frozen=True)
class PlanConsumption:
    resource_id: str
    product_id: str
    coefficient_unit: str  # resource per product, e.g. "h/piece" or "min/piece"


@dataclass(frozen=True)
class PlanningUnitsInput:
    currency: str  # objective unit, must be a single currency
    products: tuple[PlanProduct, ...]
    resources: tuple[PlanResource, ...]
    consumptions: tuple[PlanConsumption, ...]


def validate_planning_units(
    inp: PlanningUnitsInput, registry: UnitRegistry | None = None
) -> UnitReport:
    reg = registry or UnitRegistry.default()
    extra: list[UnitIssue] = []
    try:
        cur = parse_unit(inp.currency, reg)
        if not cur.dimension.exps or cur.dimension.physical_part().exps or cur.scale != 1:
            extra.append(
                UnitIssue(
                    "CURRENCY_INVALID", "objective/currency", f"{inp.currency!r} is not a currency"
                )
            )
        elif len(cur.dimension.exps) != 1 or cur.dimension.exps[0][1] != 1:
            extra.append(
                UnitIssue("CURRENCY_INVALID", "objective/currency", "not a single currency")
            )
    except UnitError as exc:
        extra.append(UnitIssue(exc.code, "objective/currency", exc.message))
    pids = {p.id for p in inp.products}
    rids = {r.id for r in inp.resources}
    if len(pids) != len(inp.products) or len(rids) != len(inp.resources):
        extra.append(UnitIssue("DUPLICATE_ID", "problem", "duplicate product or resource id"))
    variables = {f"x_{p.id}": p.unit for p in inp.products}
    cons: list[ConstraintUnits] = []
    unit_of = {r.id: r.capacity_unit for r in inp.resources}
    for r in sorted(inp.resources, key=lambda z: z.id):
        terms = []
        for c in inp.consumptions:
            if c.resource_id != r.id:
                continue
            if c.product_id not in pids:
                extra.append(
                    UnitIssue(
                        "UNKNOWN_REFERENCE", f"consumption:{r.id}/{c.product_id}", "no such product"
                    )
                )
                continue
            terms.append(Term(f"x_{c.product_id}", c.coefficient_unit))
        cons.append(ConstraintUnits(r.id, tuple(terms), unit_of[r.id]))
    for c in inp.consumptions:
        if c.resource_id not in rids:
            extra.append(
                UnitIssue(
                    "UNKNOWN_REFERENCE",
                    f"consumption:{c.resource_id}/{c.product_id}",
                    "no such resource",
                )
            )
    objective = ObjectiveUnits(
        tuple(Term(f"x_{p.id}", p.value_unit) for p in inp.products), inp.currency
    )
    bounds = [
        BoundUnits(f"{p.id}.{kind}", f"x_{p.id}", p.bound_unit or p.unit)
        for p in inp.products
        for kind in ("min_quantity", "max_quantity")
    ]
    base = validate_linear_units(variables, cons, objective, bounds, reg)
    subject = hashlib.sha256(repr((inp, reg.fingerprint())).encode()).hexdigest()
    return _report([*base.issues, *extra], subject)


def planning_units_from_problem(problem: Any) -> PlanningUnitsInput:
    """Read-only adapter for a Production Planning problem (duck-typed; nothing is modified).

    The existing model restates units as strings: value is ``<currency>/<product unit>`` and a
    consumption coefficient is ``<resource unit>/<product unit>``.
    """
    cur = str(problem.currency)
    products = tuple(
        PlanProduct(p.id, p.unit, f"{p.value_currency}/{p.unit}") for p in problem.products
    )
    resources = tuple(PlanResource(r.id, r.unit) for r in problem.resources)
    consumptions: Sequence[PlanConsumption] = tuple(
        PlanConsumption(c.resource_id, c.product_id, f"{c.resource_unit}/{c.product_unit}")
        for c in problem.consumptions
    )
    return PlanningUnitsInput(cur, products, resources, tuple(consumptions))
