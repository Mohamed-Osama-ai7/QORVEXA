"""Input differences between a baseline problem and a scenario problem (business inputs only)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.models import F
from qorvexa.industrial.production_planning.model import PlanningProblem


class Change(BaseModel):
    model_config = F
    kind: str  # capacity | demand_min | demand_max | value
    target_id: str
    old: Decimal | None
    new: Decimal | None


class InputDiff(BaseModel):
    model_config = F
    changes: tuple[Change, ...]

    def of_kind(self, kind: str) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind == kind)


def diff_problems(base: PlanningProblem, scen: PlanningProblem) -> InputDiff:
    out: list[Change] = []
    br = {r.id: r for r in base.resources}
    for r in sorted(scen.resources, key=lambda z: z.id):
        if r.id in br and br[r.id].capacity != r.capacity:
            out.append(
                Change(kind="capacity", target_id=r.id, old=br[r.id].capacity, new=r.capacity)
            )
    bp = {p.id: p for p in base.products}
    for p in sorted(scen.products, key=lambda z: z.id):
        o = bp.get(p.id)
        if o is None:
            continue
        if o.min_quantity != p.min_quantity:
            out.append(
                Change(kind="demand_min", target_id=p.id, old=o.min_quantity, new=p.min_quantity)
            )
        if o.max_quantity != p.max_quantity:
            out.append(
                Change(kind="demand_max", target_id=p.id, old=o.max_quantity, new=p.max_quantity)
            )
        if o.value_per_unit != p.value_per_unit:
            out.append(
                Change(kind="value", target_id=p.id, old=o.value_per_unit, new=p.value_per_unit)
            )
    return InputDiff(changes=tuple(out))


def assumption_codes(diff: InputDiff) -> tuple[str, ...]:
    """Unmodeled business facts implied by the changes (resolved only if the user acknowledges)."""
    codes: list[str] = []
    if diff.of_kind("capacity"):
        codes.append("CAPACITY_CHANGE_COST_NOT_MODELED")
    if diff.of_kind("demand_min") or diff.of_kind("demand_max"):
        codes.append("DEMAND_CHANGE_ACCEPTANCE_NOT_MODELED")
    if diff.of_kind("value"):
        codes.append("VALUE_CHANGE_BASIS_NOT_VALIDATED")
    return tuple(codes)
