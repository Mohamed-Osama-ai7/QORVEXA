"""Strict continuous-LP validation and exact affine derivation (S3).

Input: a GeneralizedProblemSpec that was produced by `raw_ingress.parse_problem_spec_json`
(the only path that carries the raw-JSON guarantees). Output: exact, immutable derived forms.

This module deliberately does NOT assign constraint-to-row semantics (Encoding A is not yet
approved by the owner). It validates the shape of each constraint, derives exact affine forms for
the objective and for every constraint expression, and records the constraint's relation and
bound fields verbatim so that a later, approved normalizer can turn them into ranged rows.

Strictness:
- exactly one objective, which must carry an expression AST (an empty objective list is NOT
  interpreted as a feasibility problem);
- problemType must be explicitly 'linear_programming'; multi-objective strategy must be absent or
  'single_objective';
- only continuous decision variables; unique, non-blank ids; exact bounds with typed
  BOUND_CONTRADICTION_EXACT on lower > upper;
- only relations '<=', '>=', '==', 'range'; only hard constraints;
- only AST nodes supported by the affine evaluator (a ComparisonNode is allowed only as the root
  of a constraint expression);
- client verification flags are never trusted: `mathematicallyVerified` must be false and no
  client text feeds any verification claim; `expressionSummary` is never parsed or executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Final, Literal

from qorvexa.contracts.solver_fabric import (
    ComparisonNode,
    ConstraintSpecV1_1,
    DecisionVariableSpecV1_1,
    GeneralizedProblemSpec,
    ObjectiveSpecV1_1,
)
from qorvexa.solver_fabric.affine import AffineForm, evaluate_affine_expression
from qorvexa.solver_fabric.exact import (
    BOUND_CONTRADICTION_EXACT,
    BoundContradictionError,
    Interval,
    to_fraction,
)

SUPPORTED_RELATIONS: Final[frozenset[str]] = frozenset({"<=", ">=", "==", "range"})

# Error codes
OBJECTIVE_COUNT: Final = "OBJECTIVE_COUNT"
OBJECTIVE_MISSING_AST: Final = "OBJECTIVE_MISSING_AST"
PROBLEM_TYPE_UNSUPPORTED: Final = "PROBLEM_TYPE_UNSUPPORTED"
MULTI_OBJECTIVE_UNSUPPORTED: Final = "MULTI_OBJECTIVE_UNSUPPORTED"
VARIABLE_DOMAIN_UNSUPPORTED: Final = "VARIABLE_DOMAIN_UNSUPPORTED"
DISCRETE_VALUES_NOT_ALLOWED: Final = "DISCRETE_VALUES_NOT_ALLOWED"
DUPLICATE_ID: Final = "DUPLICATE_ID"
BLANK_ID: Final = "BLANK_ID"
RELATION_UNSUPPORTED: Final = "RELATION_UNSUPPORTED"
HARDNESS_UNSUPPORTED: Final = "HARDNESS_UNSUPPORTED"
CONSTRAINT_MISSING_AST: Final = "CONSTRAINT_MISSING_AST"
CONSTRAINT_BOUND_FIELDS: Final = "CONSTRAINT_BOUND_FIELDS"
VERIFICATION_FLAG_NOT_TRUSTED: Final = "VERIFICATION_FLAG_NOT_TRUSTED"
TOO_MANY_VARIABLES: Final = "TOO_MANY_VARIABLES"
TOO_MANY_CONSTRAINTS: Final = "TOO_MANY_CONSTRAINTS"


class IntakeError(ValueError):
    """Typed strict-validation failure with a stable code."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at '{path}': {message}")
        self.code = code
        self.path = path
        self.message = message


class IntakeBoundContradictionError(IntakeError):
    """Exact variable-bound contradiction, e.g. 5 <= x <= 2 (BOUND_CONTRADICTION_EXACT)."""

    def __init__(self, path: str, variable_id: str, lower: Fraction, upper: Fraction) -> None:
        super().__init__(
            BOUND_CONTRADICTION_EXACT,
            path,
            f"variable {variable_id!r}: lower bound {lower} exceeds upper bound {upper}.",
        )
        self.variable_id = variable_id
        self.lower = lower
        self.upper = upper


@dataclass(frozen=True)
class IntakeLimits:
    max_variables: int = 100_000
    max_constraints: int = 100_000


@dataclass(frozen=True)
class VariableBounds:
    """Exact variable bounds; None means unbounded in that direction."""

    variable_id: str
    lower: Fraction | None
    upper: Fraction | None


@dataclass(frozen=True)
class DerivedObjective:
    objective_id: str
    direction: Literal["minimize", "maximize"]
    form: AffineForm


@dataclass(frozen=True)
class DerivedConstraint:
    """Exact derivation of one constraint WITHOUT row semantics (Encoding A undecided).

    shape == 'COMPARISON_ROOT': `left` and `right` are the exact forms of the comparison's sides
    and `comparison_operator` its operator. shape == 'EXPRESSION': `expression` is the exact form.
    `relation`, `bound_value` and `upper_bound_value` are the client's fields, verbatim.
    """

    constraint_id: str
    relation: str
    shape: Literal["COMPARISON_ROOT", "EXPRESSION"]
    expression: AffineForm | None
    left: AffineForm | None
    right: AffineForm | None
    comparison_operator: str | None
    bound_value: Fraction | None
    upper_bound_value: Fraction | None


@dataclass(frozen=True)
class DerivedProblem:
    variables: tuple[VariableBounds, ...]
    objective: DerivedObjective
    constraints: tuple[DerivedConstraint, ...]


def _check_ids(ids: list[str], kind: str, base: str) -> None:
    seen: set[str] = set()
    for i, ident in enumerate(ids):
        if not ident.strip():
            raise IntakeError(BLANK_ID, f"{base}[{i}].id", f"{kind} id must not be blank.")
        if ident in seen:
            raise IntakeError(DUPLICATE_ID, f"{base}[{i}].id", f"duplicate {kind} id {ident!r}.")
        seen.add(ident)


def _variable(index: int, v: DecisionVariableSpecV1_1) -> VariableBounds:
    path = f"$.decisionVariables[{index}]"
    if v.domain != "continuous":
        raise IntakeError(
            VARIABLE_DOMAIN_UNSUPPORTED,
            path,
            f"variable {v.id!r} has domain {v.domain!r}; only 'continuous' is supported.",
        )
    if v.discrete_values:
        raise IntakeError(
            DISCRETE_VALUES_NOT_ALLOWED, path, "continuous variables must not list discrete values."
        )
    try:
        interval = Interval.from_bounds(v.lower_bound, v.upper_bound)
    except BoundContradictionError as err:
        raise IntakeBoundContradictionError(path, v.id, err.lower, err.upper) from err
    lower = interval.lower.value if interval.lower.is_finite else None
    upper = interval.upper.value if interval.upper.is_finite else None
    return VariableBounds(variable_id=v.id, lower=lower, upper=upper)


def _objective(spec: GeneralizedProblemSpec) -> ObjectiveSpecV1_1:
    if len(spec.objectives) != 1:
        raise IntakeError(
            OBJECTIVE_COUNT,
            "$.objectives",
            f"exactly one objective is required for the LP slice; found {len(spec.objectives)}. "
            "An empty list is not interpreted as a feasibility problem.",
        )
    objective = spec.objectives[0]
    if objective.expression_ast is None:
        raise IntakeError(
            OBJECTIVE_MISSING_AST, "$.objectives[0]", "objective requires an expressionAst."
        )
    return objective


def _check_constraint_shape(index: int, c: ConstraintSpecV1_1) -> None:
    path = f"$.constraints[{index}]"
    if c.relation not in SUPPORTED_RELATIONS:
        raise IntakeError(
            RELATION_UNSUPPORTED,
            path,
            f"relation {c.relation!r} is not supported; allowed: {sorted(SUPPORTED_RELATIONS)}.",
        )
    if c.hardness != "hard":
        raise IntakeError(
            HARDNESS_UNSUPPORTED, path, f"only hard constraints are supported, got {c.hardness!r}."
        )
    if c.expression_ast is None:
        raise IntakeError(CONSTRAINT_MISSING_AST, path, "constraint requires an expressionAst.")
    if c.relation == "range" and (c.bound_value is None or c.upper_bound_value is None):
        raise IntakeError(
            CONSTRAINT_BOUND_FIELDS, path, "'range' requires both boundValue and upperBoundValue."
        )
    if c.relation != "range" and c.upper_bound_value is not None:
        raise IntakeError(
            CONSTRAINT_BOUND_FIELDS, path, "upperBoundValue is only valid with relation 'range'."
        )


def validate_lp_problem(
    spec: GeneralizedProblemSpec, limits: IntakeLimits | None = None
) -> tuple[tuple[VariableBounds, ...], ObjectiveSpecV1_1, tuple[ConstraintSpecV1_1, ...]]:
    """Strict shape validation. Returns exact variable bounds and the validated spec parts."""
    lim = limits or IntakeLimits()

    if spec.mathematically_verified is not False:
        raise IntakeError(
            VERIFICATION_FLAG_NOT_TRUSTED, "$.mathematicallyVerified", "client flag must be false."
        )
    if spec.problem_type != "linear_programming":
        raise IntakeError(
            PROBLEM_TYPE_UNSUPPORTED,
            "$.problemType",
            f"problemType must be 'linear_programming', got {spec.problem_type!r}.",
        )
    if spec.multi_objective_strategy not in (None, "single_objective"):
        raise IntakeError(
            MULTI_OBJECTIVE_UNSUPPORTED,
            "$.multiObjectiveStrategy",
            f"strategy {spec.multi_objective_strategy!r} is not supported.",
        )
    if len(spec.decision_variables) > lim.max_variables:
        raise IntakeError(TOO_MANY_VARIABLES, "$.decisionVariables", "too many variables.")
    if len(spec.constraints) > lim.max_constraints:
        raise IntakeError(TOO_MANY_CONSTRAINTS, "$.constraints", "too many constraints.")

    objective = _objective(spec)
    _check_ids([v.id for v in spec.decision_variables], "variable", "$.decisionVariables")
    _check_ids([c.id for c in spec.constraints], "constraint", "$.constraints")

    variables = tuple(_variable(i, v) for i, v in enumerate(spec.decision_variables))
    for i, c in enumerate(spec.constraints):
        _check_constraint_shape(i, c)
    return variables, objective, tuple(spec.constraints)


def derive_problem_forms(
    spec: GeneralizedProblemSpec, limits: IntakeLimits | None = None
) -> DerivedProblem:
    """Validate strictly, then derive exact affine forms (no row semantics)."""
    variables, objective, constraints = validate_lp_problem(spec, limits)
    domains = {v.variable_id: "continuous" for v in variables}

    assert objective.expression_ast is not None  # noqa: S101  (validated above)
    obj_form = evaluate_affine_expression(
        objective.expression_ast,
        path="$.objectives[0].expressionAst",
        variable_domains=domains,
    )
    derived_objective = DerivedObjective(
        objective_id=objective.id, direction=objective.direction, form=obj_form
    )

    derived: list[DerivedConstraint] = []
    for i, c in enumerate(constraints):
        ast = c.expression_ast
        assert ast is not None  # noqa: S101  (validated above)
        base = f"$.constraints[{i}].expressionAst"
        bound = None if c.bound_value is None else to_fraction(c.bound_value)
        upper = None if c.upper_bound_value is None else to_fraction(c.upper_bound_value)
        if isinstance(ast, ComparisonNode):
            derived.append(
                DerivedConstraint(
                    constraint_id=c.id,
                    relation=c.relation,
                    shape="COMPARISON_ROOT",
                    expression=None,
                    left=evaluate_affine_expression(
                        ast.left, path=f"{base}.left", variable_domains=domains
                    ),
                    right=evaluate_affine_expression(
                        ast.right, path=f"{base}.right", variable_domains=domains
                    ),
                    comparison_operator=ast.operator,
                    bound_value=bound,
                    upper_bound_value=upper,
                )
            )
        else:
            derived.append(
                DerivedConstraint(
                    constraint_id=c.id,
                    relation=c.relation,
                    shape="EXPRESSION",
                    expression=evaluate_affine_expression(ast, path=base, variable_domains=domains),
                    left=None,
                    right=None,
                    comparison_operator=None,
                    bound_value=bound,
                    upper_bound_value=upper,
                )
            )
    return DerivedProblem(
        variables=variables, objective=derived_objective, constraints=tuple(derived)
    )
