"""What-if scenarios: independent alternative inputs with preserved lineage (formulation only).

A scenario never mutates the base problem and is never marked as solved here; its status is always
``FORMULATED_NOT_SOLVED`` until a verified solver result exists elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from qorvexa.industrial.production_planning.compiler import (
    CompiledFormulation,
    canonical_input_json,
    compile_problem,
)
from qorvexa.industrial.production_planning.model import (
    Exact,
    Ident,
    NonNeg,
    PlanningInputError,
    PlanningProblem,
    validate_planning_dict,
)

SCENARIO_STATUS: Final = "FORMULATED_NOT_SOLVED"
_F = ConfigDict(frozen=True, extra="forbid")


class CapacityChange(BaseModel):
    model_config = _F
    kind: Literal["capacity"] = "capacity"
    resource_id: Ident
    capacity: NonNeg


class DemandBoundsChange(BaseModel):
    model_config = _F
    kind: Literal["demand_bounds"] = "demand_bounds"
    product_id: Ident
    min_quantity: NonNeg | None = None
    max_quantity: NonNeg | None = None
    clear_max: bool = False

    @model_validator(mode="after")
    def _check(self) -> DemandBoundsChange:
        if self.clear_max and self.max_quantity is not None:
            raise ValueError("clear_max and max_quantity are mutually exclusive")
        if self.min_quantity is None and self.max_quantity is None and not self.clear_max:
            raise ValueError("demand change must alter at least one bound")
        return self


class ValueChange(BaseModel):
    model_config = _F
    kind: Literal["value"] = "value"
    product_id: Ident
    value_per_unit: Exact


Change = Annotated[CapacityChange | DemandBoundsChange | ValueChange, Field(discriminator="kind")]


class ScenarioSpec(BaseModel):
    model_config = _F
    scenario_id: Ident
    description: str = Field(default="", max_length=500)
    changes: tuple[Change, ...] = Field(min_length=1, max_length=1000)
    parent_scenario_id: Ident | None = None

    @model_validator(mode="after")
    def _unique_targets(self) -> ScenarioSpec:
        seen: set[tuple[str, str]] = set()
        for c in self.changes:
            key = (c.kind, c.resource_id if isinstance(c, CapacityChange) else c.product_id)
            if key in seen:
                raise ValueError(f"ambiguous scenario: several {c.kind} changes for {key[1]}")
            seen.add(key)
        return self


@dataclass(frozen=True)
class CompiledScenario:
    scenario_id: str
    parent_scenario_id: str | None
    base_input_sha256: str
    scenario_input_sha256: str
    change_log: tuple[str, ...]
    problem: PlanningProblem
    formulation: CompiledFormulation
    status: str = SCENARIO_STATUS


def _sha(problem: PlanningProblem) -> str:
    import hashlib

    return hashlib.sha256(canonical_input_json(problem).encode("utf-8")).hexdigest()


def apply_scenario(
    base: PlanningProblem, spec: ScenarioSpec
) -> tuple[PlanningProblem, tuple[str, ...]]:
    """Return the altered problem (re-validated end to end) and a human-readable change log."""
    doc: dict[str, Any] = base.model_dump(mode="python")
    products = {p["id"]: p for p in doc["products"]}
    resources = {r["id"]: r for r in doc["resources"]}
    log: list[str] = []
    for ch in spec.changes:
        if isinstance(ch, CapacityChange):
            if ch.resource_id not in resources:
                raise PlanningInputError("UNKNOWN_REFERENCE", f"unknown resource {ch.resource_id}")
            old = resources[ch.resource_id]["capacity"]
            resources[ch.resource_id]["capacity"] = ch.capacity
            log.append(f"resource:{ch.resource_id}.capacity {old} -> {ch.capacity}")
        elif isinstance(ch, DemandBoundsChange):
            if ch.product_id not in products:
                raise PlanningInputError("UNKNOWN_REFERENCE", f"unknown product {ch.product_id}")
            p = products[ch.product_id]
            if ch.min_quantity is not None:
                log.append(
                    f"product:{ch.product_id}.min_quantity {p['min_quantity']} -> {ch.min_quantity}"
                )
                p["min_quantity"] = ch.min_quantity
            if ch.max_quantity is not None or ch.clear_max:
                new_max: Decimal | None = None if ch.clear_max else ch.max_quantity
                log.append(f"product:{ch.product_id}.max_quantity {p['max_quantity']} -> {new_max}")
                p["max_quantity"] = new_max
        else:
            if ch.product_id not in products:
                raise PlanningInputError("UNKNOWN_REFERENCE", f"unknown product {ch.product_id}")
            p = products[ch.product_id]
            log.append(
                f"product:{ch.product_id}.value_per_unit "
                f"{p['value_per_unit']} -> {ch.value_per_unit}"
            )
            p["value_per_unit"] = ch.value_per_unit
    return validate_planning_dict(doc), tuple(log)


def parse_scenario(raw: dict[str, Any]) -> ScenarioSpec:
    try:
        return ScenarioSpec.model_validate(raw)
    except ValidationError as exc:
        raise PlanningInputError("INVALID_SCENARIO", str(exc.errors()[0]["msg"])) from exc


def compile_scenario(base: PlanningProblem, spec: ScenarioSpec) -> CompiledScenario:
    problem, log = apply_scenario(base, spec)
    base_sha = _sha(base)
    lineage = (
        f"base_problem:{base.metadata.problem_id}",
        f"base_input_sha256:{base_sha}",
        f"scenario:{spec.scenario_id}",
        *((f"parent_scenario:{spec.parent_scenario_id}",) if spec.parent_scenario_id else ()),
    )
    formulation = compile_problem(
        problem, problem_id=f"{base.metadata.problem_id}::{spec.scenario_id}", lineage=lineage
    )
    return CompiledScenario(
        scenario_id=spec.scenario_id,
        parent_scenario_id=spec.parent_scenario_id,
        base_input_sha256=base_sha,
        scenario_input_sha256=_sha(problem),
        change_log=log,
        problem=problem,
        formulation=formulation,
    )
