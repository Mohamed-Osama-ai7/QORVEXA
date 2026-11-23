"""Encoding A: exact constraint-to-row normalization for the continuous-LP slice.

For a constraint expression ``E = e0 + a^T x`` (the AST is the left-hand side, the relation and
bound fields are the right-hand side) the ranged row ``lo <= a^T x <= hi`` is:

    E <= b        ->  (-inf,   b - e0)
    E >= b        ->  (b - e0, +inf)
    E == b        ->  (b - e0, b - e0)
    range [l, u]  ->  (l - e0, u - e0)

All arithmetic is exact (Fraction). Strict inequalities, ComparisonNode roots, missing or
ambiguous bounds, inverted ranges, soft constraints and unsupported relations are rejected with
typed errors. ``expression_summary`` is never read. Maximization is preserved: the canonical
builder stores the minimization form and records the original sense.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Final

from qorvexa.solver_fabric.canonical_lp import (
    CanonicalRangedLP,
    ColumnSpec,
    ModelLimits,
    RowSpec,
    build_canonical_lp,
)
from qorvexa.solver_fabric.lp_intake import DerivedConstraint, DerivedProblem

COMPARISON_NODE_UNSUPPORTED: Final = "COMPARISON_NODE_UNSUPPORTED"
STRICT_INEQUALITY_UNSUPPORTED: Final = "STRICT_INEQUALITY_UNSUPPORTED"
RELATION_NOT_SUPPORTED: Final = "ENCODING_A_RELATION_UNSUPPORTED"
BOUND_MISSING: Final = "BOUND_MISSING"
BOUND_AMBIGUOUS: Final = "BOUND_AMBIGUOUS"
RANGE_INVERTED: Final = "RANGE_INVERTED"
EXPRESSION_MISSING: Final = "EXPRESSION_MISSING"

_STRICT: Final = frozenset({"<", ">", "!=", "<>"})


class NormalizationError(ValueError):
    """Typed Encoding A failure with a stable code and the offending constraint id."""

    def __init__(self, code: str, constraint_id: str, message: str) -> None:
        super().__init__(f"{code} at constraint {constraint_id!r}: {message}")
        self.code = code
        self.constraint_id = constraint_id
        self.message = message


def normalize_constraint(c: DerivedConstraint) -> RowSpec:
    """Apply Encoding A to one derived constraint, returning an exact ranged row."""
    if c.shape != "EXPRESSION" or c.expression is None:
        if c.shape == "COMPARISON_ROOT":
            raise NormalizationError(
                COMPARISON_NODE_UNSUPPORTED,
                c.constraint_id,
                "a ComparisonNode root is not accepted; use an arithmetic expression with "
                "relation and bound fields.",
            )
        raise NormalizationError(EXPRESSION_MISSING, c.constraint_id, "no derived expression.")
    if c.left is not None or c.right is not None or c.comparison_operator is not None:
        raise NormalizationError(
            COMPARISON_NODE_UNSUPPORTED, c.constraint_id, "comparison data on an expression row."
        )

    e0 = c.expression.constant
    lo: Fraction | None
    hi: Fraction | None
    rel = c.relation
    if rel in _STRICT:
        raise NormalizationError(
            STRICT_INEQUALITY_UNSUPPORTED, c.constraint_id, f"strict relation {rel!r}."
        )
    if rel == "range":
        if c.bound_value is None or c.upper_bound_value is None:
            raise NormalizationError(
                BOUND_MISSING, c.constraint_id, "'range' needs boundValue and upperBoundValue."
            )
        if c.bound_value > c.upper_bound_value:
            raise NormalizationError(
                RANGE_INVERTED,
                c.constraint_id,
                f"range lower {c.bound_value} exceeds upper {c.upper_bound_value}.",
            )
        lo, hi = c.bound_value - e0, c.upper_bound_value - e0
    elif rel in ("<=", ">=", "=="):
        if c.bound_value is None:
            raise NormalizationError(BOUND_MISSING, c.constraint_id, "boundValue is required.")
        if c.upper_bound_value is not None:
            raise NormalizationError(
                BOUND_AMBIGUOUS,
                c.constraint_id,
                f"upperBoundValue is not allowed with relation {rel!r}.",
            )
        b = c.bound_value - e0
        lo = None if rel == "<=" else b
        hi = None if rel == ">=" else b
    else:
        raise NormalizationError(
            RELATION_NOT_SUPPORTED, c.constraint_id, f"unsupported relation {rel!r}."
        )
    return RowSpec(
        row_id=c.constraint_id, coefficients=dict(c.expression.coefficients), lower=lo, upper=hi
    )


def build_model_from_derived(
    problem: DerivedProblem, limits: ModelLimits | None = None
) -> CanonicalRangedLP:
    """Encoding A over a whole derived problem, then the exact canonical ranged-LP builder."""
    columns = [ColumnSpec(v.variable_id, v.lower, v.upper) for v in problem.variables]
    rows = [normalize_constraint(c) for c in problem.constraints]
    obj = problem.objective
    return build_canonical_lp(
        columns,
        objective_cost=dict(obj.form.coefficients),
        objective_constant=obj.form.constant,
        objective_sense=obj.direction,
        rows=rows,
        limits=limits or ModelLimits(),
    )
