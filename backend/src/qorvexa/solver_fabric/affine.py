"""Independent affine expression evaluator for QORVEXA Solver Fabric.

Directly evaluates the frozen ExprNode AST hierarchy into a canonical, deeply immutable
AffineForm: constant (exact Fraction) + coefficients (exact Fractions keyed by variable id).

Strict grammar:
- Literals: data_type real/integer, value exactly int or float (never bool/str), finite.
  Integers stay exact; floats denote their exact binary64 value.
- Variables: no time index; if a variable table is supplied every id must be declared
  and be 'continuous'.
- neg, add, sub, n-ary sum (non-empty); mul needs a *structurally* constant operand;
  div needs a nonzero *structurally* constant divisor. Structural tracking survives
  cancellation, so (x - x) * y and x / (y - y + 2) are rejected.
- Both operands of every binary node are fully validated before any simplification, so
  0 * (x * y) and 0 * (1 / 0) are rejected rather than folded to 0.
- ParameterRefNode, comparison/lookup/statistical/uncompiled nodes and nonlinear operators
  are rejected with typed errors.

Deterministic resource limits (AffineLimits): distinct nodes, edges, depth, sum arity,
numerator/denominator bit length of every intermediate value, and total work units.
Shared sub-DAGs are memoized by object identity (results are immutable, so this is safe);
cycles are detected and rejected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType

from qorvexa.contracts.solver_fabric import (
    BinaryOpNode,
    ComparisonNode,
    ExprNode,
    LiteralNode,
    LookupTableNode,
    NaryOpNode,
    ParameterRefNode,
    StatisticalDistNode,
    UnaryOpNode,
    UncompiledNode,
    VariableRefNode,
)
from qorvexa.solver_fabric.exact import (
    DEFAULT_MAX_RATIONAL_BITS,
    ExactResourceError,
    check_rational_size,
    to_fraction,
)

MAX_VARIABLE_ID_CHARS = 256

# ---------------------------------------------------------------------------
# 1. Typed Exception Hierarchy
# ---------------------------------------------------------------------------


class AffineEvaluationError(Exception):
    """Base exception for all affine expression evaluation failures."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"Affine evaluation error at '{path}': {message}")
        self.path = path
        self.message = message


class UnsupportedNodeError(AffineEvaluationError):
    """Raised when an expression node type cannot be evaluated affinely."""


class UnsupportedParameterRefError(AffineEvaluationError):
    """Raised when a ParameterRefNode is encountered (unsupported in Slice 1)."""


class UnsupportedOperatorError(AffineEvaluationError):
    """Raised when an operator cannot be evaluated in a linear/affine context."""


class NonlinearOperatorError(UnsupportedOperatorError):
    """Raised specifically for non-linear mathematical operations (abs, sqrt, sin, etc.)."""


class NonlinearMultiplicationError(AffineEvaluationError):
    """Raised when multiplying two subexpressions that structurally contain variables."""


class VariableDivisorError(AffineEvaluationError):
    """Raised when dividing by a subexpression that structurally contains variables."""


class DivisionByZeroError(AffineEvaluationError):
    """Raised when dividing by a subexpression evaluating to a zero constant."""


class UnsupportedLiteralError(AffineEvaluationError):
    """Raised when a literal has an invalid type, boolean, NaN, or infinity."""


class ContinuousVariableOnlyError(AffineEvaluationError):
    """Raised when an undeclared or non-continuous variable is referenced."""


class ExpressionDepthExceededError(AffineEvaluationError):
    """Raised when an expression AST exceeds the maximum recursion depth."""


class ResourceLimitExceededError(AffineEvaluationError):
    """Raised when a deterministic resource limit (nodes, edges, arity, bits, work) is hit."""

    def __init__(self, path: str, limit_name: str, message: str) -> None:
        super().__init__(path, f"[{limit_name}] {message}")
        self.limit_name = limit_name


class CyclicExpressionError(AffineEvaluationError):
    """Raised when the expression graph contains a cycle."""


# ---------------------------------------------------------------------------
# 2. Limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AffineLimits:
    """Deterministic evaluation limits. All counts are exact and reproducible."""

    max_nodes: int = 200_000
    max_edges: int = 400_000
    max_depth: int = 200
    max_sum_arity: int = 100_000
    max_rational_bits: int = DEFAULT_MAX_RATIONAL_BITS
    max_work: int = 10_000_000


DEFAULT_LIMITS = AffineLimits()


# ---------------------------------------------------------------------------
# 3. Canonical AffineForm (deeply immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AffineForm:
    """Canonical affine expression c_0 + sum(a_i * x_i), deeply immutable.

    - constant is an exact Fraction.
    - coefficients is a read-only mapping of non-zero Fractions (zeros stripped).
    - structural_variable_refs records every variable that structurally appeared, even
      if its coefficient cancelled.
    """

    constant: Fraction
    coefficients: Mapping[str, Fraction] = field(default_factory=dict)
    structural_variable_refs: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.constant, Fraction):
            object.__setattr__(self, "constant", to_fraction(self.constant))

        cleaned: dict[str, Fraction] = {}
        for k, v in self.coefficients.items():
            frac_v = v if isinstance(v, Fraction) else to_fraction(v)
            if frac_v != 0:
                cleaned[k] = frac_v
        object.__setattr__(self, "coefficients", MappingProxyType(cleaned))

        if not isinstance(self.structural_variable_refs, frozenset):
            object.__setattr__(
                self, "structural_variable_refs", frozenset(self.structural_variable_refs)
            )

    def __hash__(self) -> int:
        items = frozenset(self.coefficients.items())
        return hash((self.constant, items, self.structural_variable_refs))

    @property
    def is_structurally_constant(self) -> bool:
        """True if the subexpression structurally contains zero variable references."""
        return len(self.structural_variable_refs) == 0

    @property
    def is_constant(self) -> bool:
        """True if all variable coefficients are zero (algebraically constant)."""
        return len(self.coefficients) == 0


# ---------------------------------------------------------------------------
# 4. Evaluator
# ---------------------------------------------------------------------------


class _Context:
    """Per-call evaluation state: limits, counters, memo table and cycle guard."""

    def __init__(
        self,
        limits: AffineLimits,
        variable_domains: Mapping[str, str] | None,
    ) -> None:
        self.limits = limits
        self.variable_domains = variable_domains
        self.nodes = 0
        self.edges = 0
        self.work = 0
        self.memo: dict[int, AffineForm] = {}
        self.in_progress: set[int] = set()

    def charge_work(self, path: str, units: int) -> None:
        self.work += units
        if self.work > self.limits.max_work:
            raise ResourceLimitExceededError(
                path, "max_work", f"evaluation work exceeds {self.limits.max_work} units."
            )

    def charge_edge(self, path: str) -> None:
        self.edges += 1
        if self.edges > self.limits.max_edges:
            raise ResourceLimitExceededError(
                path, "max_edges", f"expression has more than {self.limits.max_edges} edges."
            )

    def checked(self, path: str, form: AffineForm) -> AffineForm:
        bits = self.limits.max_rational_bits
        try:
            check_rational_size(form.constant, bits)
            for v in form.coefficients.values():
                check_rational_size(v, bits)
        except ExactResourceError as err:
            raise ResourceLimitExceededError(path, "max_rational_bits", str(err)) from err
        return form


def evaluate_affine_expression(
    node: ExprNode,
    path: str = "$",
    variable_domains: Mapping[str, str] | None = None,
    max_depth: int | None = None,
    limits: AffineLimits | None = None,
) -> AffineForm:
    """Evaluate an ExprNode AST into a canonical AffineForm.

    Parameters:
    - node: root ExprNode.
    - path: JSON-path prefix for diagnostics.
    - variable_domains: optional variable_id -> domain table. When supplied, every referenced id
      must be declared and be 'continuous'. None disables the check (caller's responsibility).
    - max_depth: convenience override of limits.max_depth.
    - limits: deterministic resource limits (defaults to DEFAULT_LIMITS).
    """
    lim = limits or DEFAULT_LIMITS
    if max_depth is not None:
        lim = AffineLimits(
            max_nodes=lim.max_nodes,
            max_edges=lim.max_edges,
            max_depth=max_depth,
            max_sum_arity=lim.max_sum_arity,
            max_rational_bits=lim.max_rational_bits,
            max_work=lim.max_work,
        )
    ctx = _Context(lim, variable_domains)
    try:
        return _evaluate(node, path, 0, ctx)
    except RecursionError as err:
        raise ExpressionDepthExceededError(
            path, "Expression AST exhausted the interpreter recursion limit."
        ) from err


def _evaluate(node: ExprNode, path: str, depth: int, ctx: _Context) -> AffineForm:
    if depth > ctx.limits.max_depth:
        raise ExpressionDepthExceededError(
            path,
            f"Expression AST depth ({depth}) exceeds maximum permitted depth of "
            f"{ctx.limits.max_depth}.",
        )

    key = id(node)
    cached = ctx.memo.get(key)
    if cached is not None:
        return cached
    if key in ctx.in_progress:
        raise CyclicExpressionError(path, "Expression graph contains a cycle.")

    ctx.nodes += 1
    if ctx.nodes > ctx.limits.max_nodes:
        raise ResourceLimitExceededError(
            path, "max_nodes", f"expression has more than {ctx.limits.max_nodes} distinct nodes."
        )
    ctx.charge_work(path, 1)

    ctx.in_progress.add(key)
    try:
        form = ctx.checked(path, _evaluate_node(node, path, depth, ctx))
    finally:
        ctx.in_progress.discard(key)
    ctx.memo[key] = form
    return form


def _child(node: ExprNode, path: str, depth: int, ctx: _Context) -> AffineForm:
    ctx.charge_edge(path)
    return _evaluate(node, path, depth + 1, ctx)


def _literal(node: LiteralNode, path: str) -> AffineForm:
    if node.data_type not in ("real", "integer"):
        raise UnsupportedLiteralError(
            path, f"Literal data_type '{node.data_type}' is not supported in affine expressions."
        )
    raw = node.value
    # bool is a subclass of int: reject it first. Strings are never numeric here.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise UnsupportedLiteralError(
            path, f"Literal value of type '{type(raw).__name__}' is not a numeric coefficient."
        )
    try:
        frac_val = to_fraction(raw)
    except (ValueError, TypeError) as err:
        raise UnsupportedLiteralError(path, f"Invalid numeric literal '{raw}': {err}") from err
    if node.data_type == "integer" and frac_val.denominator != 1:
        raise UnsupportedLiteralError(
            path, f"Literal {raw!r} is declared integer but is not integral."
        )
    return AffineForm(constant=frac_val)


def _variable(node: VariableRefNode, path: str, ctx: _Context) -> AffineForm:
    if node.time_index is not None:
        raise UnsupportedNodeError(
            path,
            f"Time-indexed variable reference '{node.variable_id}[{node.time_index}]' "
            "is not supported in the Slice 1 LP.",
        )
    var_key = node.variable_id
    if not var_key.strip() or len(var_key) > MAX_VARIABLE_ID_CHARS:
        raise UnsupportedNodeError(
            path, f"Variable id must be 1..{MAX_VARIABLE_ID_CHARS} non-blank characters."
        )
    domains = ctx.variable_domains
    if domains is not None:
        if var_key not in domains:
            raise ContinuousVariableOnlyError(
                path, f"Variable '{var_key}' is not declared in the supplied variable table."
            )
        domain = domains[var_key]
        if not isinstance(domain, str) or domain.lower() != "continuous":
            raise ContinuousVariableOnlyError(
                path,
                f"Variable '{var_key}' has domain '{domain}'; "
                "Slice 1 LP supports continuous variables only.",
            )
    return AffineForm(
        constant=Fraction(0),
        coefficients={var_key: Fraction(1)},
        structural_variable_refs=frozenset({var_key}),
    )


def _combine(
    left: AffineForm, right: AffineForm, sign: int, path: str, ctx: _Context
) -> AffineForm:
    ctx.charge_work(path, len(left.coefficients) + len(right.coefficients) + 1)
    coeffs = dict(left.coefficients)
    for k, v in right.coefficients.items():
        coeffs[k] = coeffs.get(k, Fraction(0)) + sign * v
    return AffineForm(
        constant=left.constant + sign * right.constant,
        coefficients=coeffs,
        structural_variable_refs=left.structural_variable_refs | right.structural_variable_refs,
    )


def _scale(form: AffineForm, scale: Fraction, structural: frozenset[str]) -> AffineForm:
    return AffineForm(
        constant=form.constant * scale,
        coefficients={k: v * scale for k, v in form.coefficients.items()},
        structural_variable_refs=structural,
    )


def _evaluate_node(node: ExprNode, path: str, depth: int, ctx: _Context) -> AffineForm:
    if isinstance(node, LiteralNode):
        return _literal(node, path)

    if isinstance(node, VariableRefNode):
        return _variable(node, path, ctx)

    if isinstance(node, ParameterRefNode):
        raise UnsupportedParameterRefError(
            path, f"Parameter reference '{node.parameter_id}' is not supported in Slice 1 LP."
        )

    if isinstance(node, UnaryOpNode):
        if node.operator == "neg":
            arg = _child(node.argument, f"{path}.argument", depth, ctx)
            ctx.charge_work(path, len(arg.coefficients) + 1)
            return _scale(arg, Fraction(-1), arg.structural_variable_refs)
        raise NonlinearOperatorError(
            path, f"Unary operator '{node.operator}' is non-linear and cannot be evaluated."
        )

    if isinstance(node, BinaryOpNode):
        left_path, right_path = f"{path}.left", f"{path}.right"
        op = node.operator
        if op not in ("add", "sub", "mul", "div"):
            raise UnsupportedOperatorError(
                path, f"Binary operator '{op}' is not supported in affine expressions."
            )
        # Fully validate both operands (left first) before any simplification.
        left = _child(node.left, left_path, depth, ctx)
        right = _child(node.right, right_path, depth, ctx)

        if op == "add":
            return _combine(left, right, 1, path, ctx)
        if op == "sub":
            return _combine(left, right, -1, path, ctx)

        all_structural = left.structural_variable_refs | right.structural_variable_refs
        if op == "mul":
            if not left.is_structurally_constant and not right.is_structurally_constant:
                raise NonlinearMultiplicationError(
                    path,
                    "Cannot multiply two subexpressions that structurally contain variables "
                    f"(left contains {sorted(left.structural_variable_refs)}, "
                    f"right contains {sorted(right.structural_variable_refs)}).",
                )
            ctx.charge_work(path, len(left.coefficients) + len(right.coefficients) + 1)
            if left.is_structurally_constant:
                return _scale(right, left.constant, all_structural)
            return _scale(left, right.constant, all_structural)

        # op == "div"
        if not right.is_structurally_constant:
            raise VariableDivisorError(
                right_path,
                "Cannot divide by a subexpression that structurally contains variables "
                f"({sorted(right.structural_variable_refs)}).",
            )
        if right.constant == 0:
            raise DivisionByZeroError(
                right_path, "Division by zero constant in affine expression divisor."
            )
        ctx.charge_work(path, len(left.coefficients) + 1)
        return _scale(left, 1 / right.constant, all_structural)

    if isinstance(node, NaryOpNode):
        if node.operator != "sum":
            raise UnsupportedOperatorError(
                path, f"N-ary operator '{node.operator}' is not supported in affine expressions."
            )
        arity = len(node.arguments)
        if arity == 0:
            raise UnsupportedOperatorError(path, "Empty sum is not a valid affine expression.")
        if arity > ctx.limits.max_sum_arity:
            raise ResourceLimitExceededError(
                path,
                "max_sum_arity",
                f"sum has {arity} arguments (limit {ctx.limits.max_sum_arity}).",
            )
        const = Fraction(0)
        coeffs: dict[str, Fraction] = {}
        structural: set[str] = set()
        for idx, arg_node in enumerate(node.arguments):
            arg_form = _child(arg_node, f"{path}.arguments[{idx}]", depth, ctx)
            ctx.charge_work(path, len(arg_form.coefficients) + 1)
            const += arg_form.constant
            structural |= arg_form.structural_variable_refs
            for k, v in arg_form.coefficients.items():
                coeffs[k] = coeffs.get(k, Fraction(0)) + v
        return AffineForm(
            constant=const,
            coefficients=coeffs,
            structural_variable_refs=frozenset(structural),
        )

    if isinstance(node, (ComparisonNode, LookupTableNode, StatisticalDistNode, UncompiledNode)):
        raise UnsupportedNodeError(
            path, f"Node type '{node.type}' cannot be evaluated as an affine expression."
        )

    raise UnsupportedNodeError(path, f"Unrecognized AST node type: {type(node).__name__}")
