"""Deterministic compiler: PlanningProblem -> GeneralizedProblemSpec JSON + traceability manifest.

Formulation (continuous LP):
    x[p]  = quantity of product p                         min[p] <= x[p] <= max[p]
    capacity:   sum_p consumption[r,p] * x[p] <= capacity[r]
    MAXIMIZE_MARGIN: maximize sum_p margin[p]  * x[p]
    MINIMIZE_COST:   minimize sum_p cost[p]    * x[p]

Demand commitments and caps are *variable bounds* (not rows); the manifest maps them. The output
is the existing strict-ingress JSON (typed ExprNode ASTs); `expressionSummary` is descriptive text
only and is never executed or parsed. Ordering is by id, so input order never changes the output.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict

from qorvexa.contracts.solver_fabric import GeneralizedProblemSpec
from qorvexa.industrial.production_planning.model import (
    ObjectiveMode,
    PlanningProblem,
)
from qorvexa.solver_fabric.raw_ingress import parse_problem_spec_json

COMPILER_ID: Final = "qorvexa-production-planning/0.1.0"

_F = ConfigDict(frozen=True, extra="forbid")


class TermTrace(BaseModel):
    model_config = _F
    variable_id: str
    product_id: str
    coefficient: Decimal
    source: str  # which input record produced this term


class VariableTrace(BaseModel):
    model_config = _F
    variable_id: str
    product_id: str
    product_name: str
    unit: str
    lower: Decimal
    upper: Decimal | None
    lower_source: str
    upper_source: str | None


class ConstraintTrace(BaseModel):
    model_config = _F
    constraint_id: str
    kind: str  # RESOURCE_CAPACITY
    resource_id: str
    resource_name: str
    unit: str
    capacity: Decimal
    terms: tuple[TermTrace, ...]
    source: str


class ObjectiveTrace(BaseModel):
    model_config = _F
    objective_id: str
    mode: ObjectiveMode
    direction: str
    currency: str
    constant: Decimal
    terms: tuple[TermTrace, ...]


class FormulationManifest(BaseModel):
    model_config = _F
    compiler_id: str
    problem_id: str
    input_sha256: str
    spec_sha256: str
    mode: ObjectiveMode
    variables: tuple[VariableTrace, ...]
    constraints: tuple[ConstraintTrace, ...]
    objective: ObjectiveTrace
    lineage: tuple[str, ...] = ()

    def variable_for_product(self, product_id: str) -> VariableTrace:
        for v in self.variables:
            if v.product_id == product_id:
                return v
        raise KeyError(product_id)


@dataclass(frozen=True)
class CompiledFormulation:
    """Compiler output. ``spec_json`` feeds the existing strict ingress unchanged."""

    spec_json: str
    manifest: FormulationManifest

    def to_spec(self) -> GeneralizedProblemSpec:
        """Run the compiled JSON through the real strict ingress (never bypassed)."""
        return parse_problem_spec_json(self.spec_json)


# ------------------------------------------------------------------ JSON rendering (exact decimals)


def _num(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0", "-") else s


def _json(o: Any) -> str:
    import json

    if isinstance(o, dict):
        return "{" + ",".join(f"{json.dumps(k)}:{_json(v)}" for k, v in o.items()) + "}"
    if isinstance(o, list | tuple):
        return "[" + ",".join(_json(v) for v in o) + "]"
    if isinstance(o, Decimal):
        return _num(o)
    return json.dumps(o, ensure_ascii=True)


def _lit(d: Decimal) -> dict[str, Any]:
    return {"type": "literal", "dataType": "real", "value": d}


def _term(coef: Decimal, vid: str) -> dict[str, Any]:
    return {
        "type": "binary_op",
        "operator": "mul",
        "left": _lit(coef),
        "right": {"type": "variable_ref", "variableId": vid},
    }


def _sum(terms: list[dict[str, Any]]) -> dict[str, Any]:
    if not terms:
        return _lit(Decimal(0))
    if len(terms) == 1:
        return terms[0]
    return {"type": "nary_op", "operator": "sum", "arguments": terms}


def canonical_input_json(problem: PlanningProblem) -> str:
    """Order-independent canonical rendering of the *input* (used for the input hash)."""
    p = problem
    doc = {
        "metadata": p.metadata.model_dump(),
        "mode": p.mode.value,
        "currency": p.currency,
        "products": [
            {
                "id": x.id,
                "name": x.name,
                "unit": x.unit,
                "value_per_unit": x.value_per_unit,
                "value_currency": x.value_currency,
                "min_quantity": x.min_quantity,
                "max_quantity": x.max_quantity,
            }
            for x in sorted(p.products, key=lambda z: z.id)
        ],
        "resources": [
            {
                "id": r.id,
                "name": r.name,
                "kind": r.kind.value,
                "unit": r.unit,
                "capacity": r.capacity,
            }
            for r in sorted(p.resources, key=lambda z: z.id)
        ],
        "consumptions": [
            {
                "resource_id": c.resource_id,
                "product_id": c.product_id,
                "amount_per_unit": c.amount_per_unit,
                "resource_unit": c.resource_unit,
                "product_unit": c.product_unit,
            }
            for c in sorted(p.consumptions, key=lambda z: (z.resource_id, z.product_id))
        ],
    }
    return _json(doc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def variable_id(index: int) -> str:
    return f"VAR-{index:04d}"


def capacity_constraint_id(index: int) -> str:
    return f"CAP-{index:04d}"


OBJECTIVE_ID: Final = "OBJ-0001"


def compile_problem(
    problem: PlanningProblem, *, problem_id: str | None = None, lineage: tuple[str, ...] = ()
) -> CompiledFormulation:
    """Compile; the result is re-parsed by the real ingress so an invalid output cannot escape."""
    prods = sorted(problem.products, key=lambda z: z.id)
    ress = sorted(problem.resources, key=lambda z: z.id)
    vid = {p.id: variable_id(i + 1) for i, p in enumerate(prods)}
    cons = {(c.resource_id, c.product_id): c for c in problem.consumptions}
    maximize = problem.mode is ObjectiveMode.MAXIMIZE_MARGIN
    value_word = "margin" if maximize else "cost"

    variables_json = []
    var_traces = []
    for p in prods:
        variables_json.append(
            {
                "id": vid[p.id],
                "name": f"Production of {p.name}",
                "symbol": f"x_{p.id}",
                "domain": "continuous",
                "unit": p.unit,
                "lowerBound": p.min_quantity,
                "upperBound": p.max_quantity,
                "description": f"Quantity of product {p.id} produced",
                "epistemicStatus": "extracted_fact",
            }
        )
        var_traces.append(
            VariableTrace(
                variable_id=vid[p.id],
                product_id=p.id,
                product_name=p.name,
                unit=p.unit,
                lower=p.min_quantity,
                upper=p.max_quantity,
                lower_source=f"product:{p.id}.min_quantity",
                upper_source=None if p.max_quantity is None else f"product:{p.id}.max_quantity",
            )
        )

    obj_terms = [
        TermTrace(
            variable_id=vid[p.id],
            product_id=p.id,
            coefficient=p.value_per_unit,
            source=f"product:{p.id}.value_per_unit",
        )
        for p in prods
    ]
    objective_json = {
        "id": OBJECTIVE_ID,
        "direction": "maximize" if maximize else "minimize",
        "name": "Total contribution margin" if maximize else "Total variable production cost",
        "expressionSummary": f"sum over products of {value_word}[p] * x[p]",
        "expressionAst": _sum([_term(t.coefficient, t.variable_id) for t in obj_terms]),
        "unit": problem.currency,
        "epistemicStatus": "extracted_fact",
    }

    constraints_json = []
    con_traces = []
    for i, r in enumerate(ress):
        terms = [
            TermTrace(
                variable_id=vid[p.id],
                product_id=p.id,
                coefficient=cons[(r.id, p.id)].amount_per_unit,
                source=f"consumption:{r.id}:{p.id}",
            )
            for p in prods
            if (r.id, p.id) in cons
        ]
        cid = capacity_constraint_id(i + 1)
        constraints_json.append(
            {
                "id": cid,
                "name": f"Capacity of {r.name}",
                "category": "capacity",
                "relation": "<=",
                "expressionSummary": f"sum of consumption[{r.id},p] * x[p] <= capacity",
                "expressionAst": _sum([_term(t.coefficient, t.variable_id) for t in terms]),
                "boundValue": r.capacity,
                "unit": r.unit,
                "hardness": "hard",
                "epistemicStatus": "extracted_fact",
            }
        )
        con_traces.append(
            ConstraintTrace(
                constraint_id=cid,
                kind="RESOURCE_CAPACITY",
                resource_id=r.id,
                resource_name=r.name,
                unit=r.unit,
                capacity=r.capacity,
                terms=tuple(terms),
                source=f"resource:{r.id}.capacity",
            )
        )

    pid = problem.metadata.problem_id if problem_id is None else problem_id
    units = sorted({p.unit for p in prods} | {r.unit for r in ress} | {problem.currency})
    doc = {
        "schemaVersion": "1.1.0",
        "problemId": pid,
        "title": problem.metadata.title,
        "problemDescription": problem.metadata.description or problem.metadata.title,
        "problemType": "linear_programming",
        "timeHorizon": {
            "isSpecified": False,
            "notes": problem.metadata.period_label,
        },
        "units": [{"quantity": "declared", "unit": u, "source": "extracted_fact"} for u in units],
        "decisionVariables": variables_json,
        "objectives": [objective_json],
        "constraints": constraints_json,
        "solverHandoffNotes": {"recommendedSolverFamily": "lp"},
    }
    spec_json = _json(doc)
    manifest = FormulationManifest(
        compiler_id=COMPILER_ID,
        problem_id=pid,
        input_sha256=_sha(canonical_input_json(problem)),
        spec_sha256=_sha(spec_json),
        mode=problem.mode,
        variables=tuple(var_traces),
        constraints=tuple(con_traces),
        objective=ObjectiveTrace(
            objective_id=OBJECTIVE_ID,
            mode=problem.mode,
            direction="maximize" if maximize else "minimize",
            currency=problem.currency,
            constant=Decimal(0),
            terms=tuple(obj_terms),
        ),
        lineage=lineage,
    )
    compiled = CompiledFormulation(spec_json, manifest)
    compiled.to_spec()  # strict ingress must accept our own output
    return compiled
