"""Canonical exact ranged-LP model, builder, and checked binary64 lowering (S3).

Canonical form (minimization, ranged rows, boxed columns):

    minimize    c^T x + c0
    subject to  rl_i <= a_i^T x <= ru_i      (i = 1..m; rl_i may be -inf, ru_i may be +inf)
                xl_j <= x_j <= xu_j          (j = 1..n; likewise infinite where absent)

All data are exact rationals (Fractions). A maximization objective is converted to minimization
by negating c and c0; the original sense is recorded so results can be mapped back.

This module does NOT decide how a client constraint (relation + AST + bound fields) becomes a
row: that mapping (Encoding A) awaits owner approval. The builder accepts explicit RowSpec
objects, so it is independent of that decision.

Identity:
- `model_sha256` hashes the exact canonical model (declaration order is part of the identity).
- `ir_sha256` hashes the lowered binary64 IR, bit for bit.

Honesty boundary: `LoweringReport` is what the COMPILER observed. It is not independent
verification. No independent compiled-IR verifier exists yet, so nothing here produces a
`LoweringEvidence` / VERIFIED MODEL_LOWERING_VALIDATED claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Final, Literal

from qorvexa.contracts.solver_runtime import RoundingSummary
from qorvexa.solver_fabric.exact import (
    DEFAULT_MAX_RATIONAL_BITS,
    CoefficientOverflowError,
    CoefficientUnderflowError,
    ExactResourceError,
    check_rational_size,
    is_subnormal_binary64,
    lower_to_binary64,
)

COMPILER_VERSION: Final = "qorvexa-lp-compiler/0.1.0-s3"
MODEL_FORMAT: Final = "qorvexa-canonical-ranged-lp/1"
IR_FORMAT: Final = b"QORVEXA-LP-IR/1\n"
MAX_DIAGNOSTICS: Final = 20

# Error codes
UNKNOWN_VARIABLE: Final = "UNKNOWN_VARIABLE"
DUPLICATE_VARIABLE: Final = "DUPLICATE_VARIABLE"
DUPLICATE_ROW: Final = "DUPLICATE_ROW"
BLANK_ID: Final = "BLANK_ID"
VARIABLE_BOUND_CONTRADICTION: Final = "BOUND_CONTRADICTION_EXACT"
ROW_BOUND_CONTRADICTION: Final = "ROW_BOUND_CONTRADICTION_EXACT"
MODEL_TOO_LARGE: Final = "MODEL_TOO_LARGE"
VALUE_TOO_LARGE: Final = "VALUE_TOO_LARGE"
COEFFICIENT_OVERFLOW: Final = "COEFFICIENT_OVERFLOW"
COEFFICIENT_UNDERFLOW: Final = "COEFFICIENT_UNDERFLOW"


class CanonicalLPError(ValueError):
    """Typed model-construction or lowering failure with a stable code."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(f"{code} at '{location}': {message}")
        self.code = code
        self.location = location
        self.message = message


@dataclass(frozen=True)
class ColumnSpec:
    variable_id: str
    lower: Fraction | None
    upper: Fraction | None


@dataclass(frozen=True)
class RowSpec:
    """One explicit ranged row: lower <= sum(coefficients[v] * v) <= upper."""

    row_id: str
    coefficients: Mapping[str, Fraction]
    lower: Fraction | None
    upper: Fraction | None


@dataclass(frozen=True)
class ModelLimits:
    max_columns: int = 100_000
    max_rows: int = 100_000
    max_nonzeros: int = 2_000_000
    max_rational_bits: int = DEFAULT_MAX_RATIONAL_BITS


@dataclass(frozen=True)
class CanonicalRangedLP:
    """Exact canonical ranged LP in minimization form. Immutable (tuples, Fractions)."""

    variable_ids: tuple[str, ...]
    col_lower: tuple[Fraction | None, ...]
    col_upper: tuple[Fraction | None, ...]
    cost: tuple[Fraction, ...]
    offset: Fraction
    original_sense: Literal["minimize", "maximize"]
    row_ids: tuple[str, ...]
    row_lower: tuple[Fraction | None, ...]
    row_upper: tuple[Fraction | None, ...]
    rows: tuple[tuple[tuple[int, Fraction], ...], ...]  # per row: sorted (col_index, coef)
    empty_infeasible_rows: tuple[str, ...] = field(default=())

    @property
    def num_variables(self) -> int:
        return len(self.variable_ids)

    @property
    def num_rows(self) -> int:
        return len(self.row_ids)

    @property
    def num_nonzeros(self) -> int:
        return sum(len(r) for r in self.rows)

    def canonical_bytes(self) -> bytes:
        def q(v: Fraction | None) -> str | None:
            return None if v is None else str(v)

        doc = {
            "format": MODEL_FORMAT,
            "sense": self.original_sense,
            "variables": list(self.variable_ids),
            "colLower": [q(v) for v in self.col_lower],
            "colUpper": [q(v) for v in self.col_upper],
            "cost": [q(v) for v in self.cost],
            "offset": q(self.offset),
            "rowIds": list(self.row_ids),
            "rowLower": [q(v) for v in self.row_lower],
            "rowUpper": [q(v) for v in self.row_upper],
            "rows": [[[j, q(a)] for j, a in row] for row in self.rows],
        }
        return json.dumps(doc, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    @property
    def model_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _check_size(value: Fraction | None, bits: int, location: str) -> None:
    if value is None:
        return
    try:
        check_rational_size(value, bits)
    except ExactResourceError as err:
        raise CanonicalLPError(VALUE_TOO_LARGE, location, str(err)) from err


def build_canonical_lp(
    columns: Sequence[ColumnSpec],
    *,
    objective_cost: Mapping[str, Fraction],
    objective_constant: Fraction,
    objective_sense: Literal["minimize", "maximize"],
    rows: Sequence[RowSpec],
    limits: ModelLimits | None = None,
) -> CanonicalRangedLP:
    """Validate and canonicalize an exact ranged LP.

    Declaration order of columns and rows is preserved (and is part of the model identity).
    Zero coefficients are dropped. Typed exact contradictions are raised for column and row bounds.
    """
    lim = limits or ModelLimits()
    if len(columns) > lim.max_columns:
        raise CanonicalLPError(MODEL_TOO_LARGE, "columns", f"more than {lim.max_columns} columns.")
    if len(rows) > lim.max_rows:
        raise CanonicalLPError(MODEL_TOO_LARGE, "rows", f"more than {lim.max_rows} rows.")

    index: dict[str, int] = {}
    for j, col in enumerate(columns):
        if not col.variable_id.strip():
            raise CanonicalLPError(BLANK_ID, f"columns[{j}]", "variable id must not be blank.")
        if col.variable_id in index:
            raise CanonicalLPError(
                DUPLICATE_VARIABLE, f"columns[{j}]", f"duplicate variable {col.variable_id!r}."
            )
        index[col.variable_id] = j
        _check_size(col.lower, lim.max_rational_bits, f"columns[{j}].lower")
        _check_size(col.upper, lim.max_rational_bits, f"columns[{j}].upper")
        if col.lower is not None and col.upper is not None and col.lower > col.upper:
            raise CanonicalLPError(
                VARIABLE_BOUND_CONTRADICTION,
                f"columns[{j}]",
                f"variable {col.variable_id!r}: lower {col.lower} exceeds upper {col.upper}.",
            )

    sign = Fraction(-1) if objective_sense == "maximize" else Fraction(1)
    cost = [Fraction(0)] * len(columns)
    for var_id, coef in objective_cost.items():
        if var_id not in index:
            raise CanonicalLPError(UNKNOWN_VARIABLE, "objective", f"unknown variable {var_id!r}.")
        _check_size(coef, lim.max_rational_bits, f"objective.{var_id}")
        cost[index[var_id]] = sign * coef
    _check_size(objective_constant, lim.max_rational_bits, "objective.constant")

    row_ids: list[str] = []
    row_lower: list[Fraction | None] = []
    row_upper: list[Fraction | None] = []
    matrix: list[tuple[tuple[int, Fraction], ...]] = []
    empty_infeasible: list[str] = []
    seen_rows: set[str] = set()
    nnz = 0
    for i, row in enumerate(rows):
        loc = f"rows[{i}]"
        if not row.row_id.strip():
            raise CanonicalLPError(BLANK_ID, loc, "row id must not be blank.")
        if row.row_id in seen_rows:
            raise CanonicalLPError(DUPLICATE_ROW, loc, f"duplicate row {row.row_id!r}.")
        seen_rows.add(row.row_id)
        _check_size(row.lower, lim.max_rational_bits, f"{loc}.lower")
        _check_size(row.upper, lim.max_rational_bits, f"{loc}.upper")
        if row.lower is not None and row.upper is not None and row.lower > row.upper:
            raise CanonicalLPError(
                ROW_BOUND_CONTRADICTION,
                loc,
                f"row {row.row_id!r}: lower {row.lower} exceeds upper {row.upper}.",
            )
        entries: list[tuple[int, Fraction]] = []
        for var_id, coef in row.coefficients.items():
            if var_id not in index:
                raise CanonicalLPError(
                    UNKNOWN_VARIABLE, loc, f"row {row.row_id!r} references unknown {var_id!r}."
                )
            _check_size(coef, lim.max_rational_bits, f"{loc}.{var_id}")
            if coef != 0:
                entries.append((index[var_id], coef))
        entries.sort(key=lambda e: e[0])
        nnz += len(entries)
        if nnz > lim.max_nonzeros:
            raise CanonicalLPError(
                MODEL_TOO_LARGE, "rows", f"more than {lim.max_nonzeros} nonzero coefficients."
            )
        if not entries:
            lo_ok = row.lower is None or row.lower <= 0
            up_ok = row.upper is None or row.upper >= 0
            if not (lo_ok and up_ok):
                empty_infeasible.append(row.row_id)
        row_ids.append(row.row_id)
        row_lower.append(row.lower)
        row_upper.append(row.upper)
        matrix.append(tuple(entries))

    return CanonicalRangedLP(
        variable_ids=tuple(c.variable_id for c in columns),
        col_lower=tuple(c.lower for c in columns),
        col_upper=tuple(c.upper for c in columns),
        cost=tuple(cost),
        offset=sign * objective_constant,
        original_sense=objective_sense,
        row_ids=tuple(row_ids),
        row_lower=tuple(row_lower),
        row_upper=tuple(row_upper),
        rows=tuple(matrix),
        empty_infeasible_rows=tuple(empty_infeasible),
    )


# ---------------------------------------------------------------------------
# Lowering to binary64
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoweringDiagnostic:
    code: str
    location: str
    message: str


@dataclass(frozen=True)
class LoweredLP:
    """Binary64 compiled IR (CSR). Infinite bounds are +/-inf; all data are finite otherwise."""

    num_variables: int
    num_rows: int
    cost: tuple[float, ...]
    col_lower: tuple[float, ...]
    col_upper: tuple[float, ...]
    row_lower: tuple[float, ...]
    row_upper: tuple[float, ...]
    row_start: tuple[int, ...]
    col_index: tuple[int, ...]
    values: tuple[float, ...]
    offset: float

    @property
    def ir_sha256(self) -> str:
        h = hashlib.sha256()
        h.update(IR_FORMAT)
        h.update(struct.pack("<QQQ", self.num_variables, self.num_rows, len(self.values)))
        for arr in (self.cost, self.col_lower, self.col_upper, self.row_lower, self.row_upper):
            h.update(struct.pack(f"<{len(arr)}d", *arr))
        h.update(struct.pack(f"<{len(self.row_start)}q", *self.row_start))
        h.update(struct.pack(f"<{len(self.col_index)}q", *self.col_index))
        h.update(struct.pack(f"<{len(self.values)}d", *self.values))
        h.update(struct.pack("<d", self.offset))
        return h.hexdigest()


@dataclass(frozen=True)
class LoweringReport:
    """Compiler-side observations about the lowering. NOT independent verification."""

    compiler_version: str
    model_sha256: str
    ir_sha256: str
    num_variables: int
    num_rows: int
    num_nonzeros: int
    values_total: int
    values_rounded: int
    subnormal_count: int
    max_abs_rounding_error: Fraction
    empty_infeasible_rows: tuple[str, ...]
    diagnostics: tuple[LoweringDiagnostic, ...]

    def rounding_summary(self) -> RoundingSummary:
        """The S1 RoundingSummary DTO populated with compiler-observed facts."""
        return RoundingSummary(
            coefficients_total=self.values_total,
            coefficients_rounded=self.values_rounded,
            subnormal_count=self.subnormal_count,
            max_abs_rounding_error=str(self.max_abs_rounding_error),
        )


class _Lowerer:
    def __init__(self) -> None:
        self.total = 0
        self.rounded = 0
        self.subnormal = 0
        self.max_err = Fraction(0)
        self.rounded_locations: list[str] = []
        self.diagnostics: list[LoweringDiagnostic] = []

    def lower(self, value: Fraction, location: str) -> float:
        try:
            result = lower_to_binary64(value)
        except CoefficientOverflowError as err:
            raise CanonicalLPError(
                COEFFICIENT_OVERFLOW, location, "value overflows IEEE-754 binary64."
            ) from err
        except CoefficientUnderflowError as err:
            raise CanonicalLPError(
                COEFFICIENT_UNDERFLOW, location, "nonzero value underflows to zero in binary64."
            ) from err
        self.total += 1
        if not result.is_exact:
            self.rounded += 1
            self.max_err = max(self.max_err, abs(result.rounding_error))
            if len(self.rounded_locations) < MAX_DIAGNOSTICS:
                self.rounded_locations.append(location)
        if is_subnormal_binary64(result.value):
            self.subnormal += 1
            if len(self.diagnostics) < MAX_DIAGNOSTICS:
                self.diagnostics.append(
                    LoweringDiagnostic(
                        "SUBNORMAL_COEFFICIENT", location, "value lowered to a subnormal binary64."
                    )
                )
        return result.value

    def bound(self, value: Fraction | None, infinity: float, location: str) -> float:
        return infinity if value is None else self.lower(value, location)


def lower_ranged_lp(lp: CanonicalRangedLP) -> tuple[LoweredLP, LoweringReport]:
    """Lower the exact model to binary64, rejecting overflow and nonzero-to-zero underflow."""
    lw = _Lowerer()
    inf = math.inf
    cost = tuple(lw.lower(c, f"cost[{lp.variable_ids[j]}]") for j, c in enumerate(lp.cost))
    col_lower = tuple(
        lw.bound(v, -inf, f"colLower[{lp.variable_ids[j]}]") for j, v in enumerate(lp.col_lower)
    )
    col_upper = tuple(
        lw.bound(v, inf, f"colUpper[{lp.variable_ids[j]}]") for j, v in enumerate(lp.col_upper)
    )
    row_lower = tuple(
        lw.bound(v, -inf, f"rowLower[{lp.row_ids[i]}]") for i, v in enumerate(lp.row_lower)
    )
    row_upper = tuple(
        lw.bound(v, inf, f"rowUpper[{lp.row_ids[i]}]") for i, v in enumerate(lp.row_upper)
    )

    row_start = [0]
    col_index: list[int] = []
    values: list[float] = []
    for i, row in enumerate(lp.rows):
        for j, coef in row:
            col_index.append(j)
            values.append(lw.lower(coef, f"a[{lp.row_ids[i]},{lp.variable_ids[j]}]"))
        row_start.append(len(values))
    offset = lw.lower(lp.offset, "offset")

    ir = LoweredLP(
        num_variables=lp.num_variables,
        num_rows=lp.num_rows,
        cost=cost,
        col_lower=col_lower,
        col_upper=col_upper,
        row_lower=row_lower,
        row_upper=row_upper,
        row_start=tuple(row_start),
        col_index=tuple(col_index),
        values=tuple(values),
        offset=offset,
    )

    diagnostics = list(lw.diagnostics)
    for loc in lw.rounded_locations:
        diagnostics.append(
            LoweringDiagnostic("ROUNDED_COEFFICIENT", loc, "value is not exactly representable.")
        )
    if lw.rounded > len(lw.rounded_locations):
        diagnostics.append(
            LoweringDiagnostic(
                "ROUNDED_COEFFICIENT_SUMMARY",
                "*",
                f"{lw.rounded} values rounded in total; first {len(lw.rounded_locations)} listed.",
            )
        )
    for row_id in lp.empty_infeasible_rows:
        diagnostics.append(
            LoweringDiagnostic(
                "EMPTY_ROW_INFEASIBLE",
                f"row[{row_id}]",
                "row has no nonzero coefficient and its range excludes 0.",
            )
        )

    report = LoweringReport(
        compiler_version=COMPILER_VERSION,
        model_sha256=lp.model_sha256,
        ir_sha256=ir.ir_sha256,
        num_variables=lp.num_variables,
        num_rows=lp.num_rows,
        num_nonzeros=len(values),
        values_total=lw.total,
        values_rounded=lw.rounded,
        subnormal_count=lw.subnormal,
        max_abs_rounding_error=lw.max_err,
        empty_infeasible_rows=lp.empty_infeasible_rows,
        diagnostics=tuple(diagnostics),
    )
    return ir, report
