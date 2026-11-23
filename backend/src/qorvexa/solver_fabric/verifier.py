"""Independent exact verifier for the continuous-LP slice (S3, Phase A).

Independence: this module imports only the immutable canonical model and IR *data types*. It
re-implements every mathematical check with plain ``Fraction`` arithmetic and trusts nothing a
solver reports (status, objective, flags). Raw solver vectors are only *candidates*; they become
evidence only if they pass the exact checks below.

Canonical form (minimization): min c^T x + c0  s.t.  rl <= A x <= ru,  xl <= x <= xu.

* Dual bound (weak duality, valid for ANY row multipliers y):
      D(y) = c0 + sum_j h(X_j, r_j) + sum_i h(R_i, y_i),   r = c - A^T y,
  where h(I, s) = inf_{t in I} s*t (None = -infinity). Rows use free-sign multipliers.
* Farkas (infeasibility): D0(y) = sum_j h(X_j, -(A^T y)_j) + sum_i h(R_i, y_i) > 0 (c0 excluded).
* Unboundedness: exactly feasible anchor x0 plus nonzero ray d with c^T d < 0 whose recession
  conditions hold: d_j > 0 needs xu_j = inf, d_j < 0 needs xl_j = -inf; (A d)_i > 0 needs
  ru_i = inf, (A d)_i < 0 needs rl_i = -inf.
* Primal feasibility: exact max violation of the candidate (taken as exact rationals of the
  binary64 values); "exactly feasible" iff the violation is 0, "numerically feasible" iff it is
  within the policy tolerance.
* Optimality is only ever a NUMERICAL gap between an exactly recomputed primal objective and an
  exactly recomputed dual bound. No exact optimality proof is produced here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Final

from qorvexa.solver_fabric.canonical_lp import (
    CanonicalRangedLP,
    LoweredLP,
    LoweringReport,
)

VERIFIER_VERSION: Final = "qorvexa-lp-verifier/0.1.0-s3"
GAP_POLICY_ID: Final = "qorvexa-gap-numerical/1"


@dataclass(frozen=True)
class VerificationPolicy:
    """Numerical acceptance policy. Exact checks never use these tolerances."""

    policy_id: str = GAP_POLICY_ID
    primal_tolerance: float = 1e-6
    absolute_gap_tolerance: float = 1e-6
    relative_gap_tolerance: float = 1e-6


# --------------------------------------------------------------------------------------
# exact helpers
# --------------------------------------------------------------------------------------


def exact_vector(values: Sequence[float], expected: int, name: str) -> list[Fraction]:
    """Exact rationals of finite binary64 values; rejects wrong length, NaN, infinity."""
    if len(values) != expected:
        raise ValueError(f"{name} has {len(values)} entries; expected {expected}.")
    out: list[Fraction] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int | float | Fraction):
            raise ValueError(f"{name} contains a non-numeric entry.")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"{name} contains a non-finite entry.")
        out.append(Fraction(v))
    return out


def vector_variants(values: Sequence[float], expected: int, name: str) -> list[list[Fraction]]:
    """Exact candidate plus lowest-denominator roundings (all still verified exactly)."""
    exact = exact_vector(values, expected, name)
    variants = [exact]
    for cap in (10**6, 10**9):
        v = [x.limit_denominator(cap) for x in exact]
        if v not in variants:
            variants.append(v)
    return variants


def _h(lo: Fraction | None, hi: Fraction | None, s: Fraction) -> Fraction | None:
    """inf over t in [lo, hi] of s*t; None means -infinity."""
    if s == 0:
        return Fraction(0)
    if s > 0:
        return None if lo is None else s * lo
    return None if hi is None else s * hi


def _a_transpose_y(m: CanonicalRangedLP, y: Sequence[Fraction]) -> list[Fraction]:
    out = [Fraction(0)] * m.num_variables
    for i, row in enumerate(m.rows):
        yi = y[i]
        if yi != 0:
            for j, a in row:
                out[j] += a * yi
    return out


def _sum_h(
    m: CanonicalRangedLP, reduced: Sequence[Fraction], y: Sequence[Fraction]
) -> Fraction | None:
    total = Fraction(0)
    for j in range(m.num_variables):
        t = _h(m.col_lower[j], m.col_upper[j], reduced[j])
        if t is None:
            return None
        total += t
    for i in range(m.num_rows):
        t = _h(m.row_lower[i], m.row_upper[i], y[i])
        if t is None:
            return None
        total += t
    return total


def dual_bound_min_form(m: CanonicalRangedLP, y: Sequence[Fraction]) -> Fraction | None:
    """D(y) in minimization form (c0 included); None means -infinity (unusable bound)."""
    if len(y) != m.num_rows:
        raise ValueError("multiplier vector length does not match the row count.")
    aty = _a_transpose_y(m, y)
    reduced = [m.cost[j] - aty[j] for j in range(m.num_variables)]
    s = _sum_h(m, reduced, y)
    return None if s is None else s + m.offset


def farkas_value(m: CanonicalRangedLP, y: Sequence[Fraction]) -> Fraction | None:
    """D0(y) with the objective (cost and constant) excluded; > 0 proves infeasibility."""
    if len(y) != m.num_rows:
        raise ValueError("multiplier vector length does not match the row count.")
    aty = _a_transpose_y(m, y)
    return _sum_h(m, [-v for v in aty], y)


# --------------------------------------------------------------------------------------
# primal feasibility
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PrimalCheck:
    max_bound_violation: Fraction
    max_row_violation: Fraction
    objective_min_form: Fraction

    @property
    def max_violation(self) -> Fraction:
        return max(self.max_bound_violation, self.max_row_violation)

    @property
    def exactly_feasible(self) -> bool:
        return self.max_violation == 0


def check_primal(m: CanonicalRangedLP, x: Sequence[Fraction]) -> PrimalCheck:
    if len(x) != m.num_variables:
        raise ValueError("primal vector length does not match the variable count.")
    zero = Fraction(0)
    bound_v = zero
    for j, xj in enumerate(x):
        lo, hi = m.col_lower[j], m.col_upper[j]
        if lo is not None and lo - xj > bound_v:
            bound_v = lo - xj
        if hi is not None and xj - hi > bound_v:
            bound_v = xj - hi
    row_v = zero
    for i, row in enumerate(m.rows):
        act = sum((a * x[j] for j, a in row), zero)
        lo, hi = m.row_lower[i], m.row_upper[i]
        if lo is not None and lo - act > row_v:
            row_v = lo - act
        if hi is not None and act - hi > row_v:
            row_v = act - hi
    obj = sum((m.cost[j] * x[j] for j in range(m.num_variables)), zero) + m.offset
    return PrimalCheck(bound_v, row_v, obj)


def to_original_sense(m: CanonicalRangedLP, min_form_value: Fraction) -> Fraction:
    return -min_form_value if m.original_sense == "maximize" else min_form_value


def safe_float(value: Fraction) -> float | None:
    """Correctly rounded float, or None if it would overflow or be non-finite."""
    try:
        f = float(value)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


# --------------------------------------------------------------------------------------
# infeasibility certificates
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FarkasVerdict:
    verified: bool
    multipliers: tuple[Fraction, ...] = ()
    certificate_value: Fraction | None = None
    source: str = ""


def verify_farkas(
    m: CanonicalRangedLP, y: Sequence[Fraction], source: str = "candidate"
) -> FarkasVerdict:
    value = farkas_value(m, y)
    if value is not None and value > 0 and any(v != 0 for v in y):
        return FarkasVerdict(True, tuple(y), value, source)
    return FarkasVerdict(False, tuple(y), value, source)


def find_interval_conflict(m: CanonicalRangedLP) -> FarkasVerdict | None:
    """Exact single-row interval conflict (incl. empty rows) as an exact Farkas certificate.

    If the activity range of a row over the variable box is disjoint from its row range, the unit
    multiplier +/- e_i is a Farkas certificate; it is re-verified through `verify_farkas`.
    """
    for i, row in enumerate(m.rows):
        amin: Fraction | None = Fraction(0)
        amax: Fraction | None = Fraction(0)
        for j, a in row:
            lo, hi = m.col_lower[j], m.col_upper[j]
            lo_term, hi_term = (lo, hi) if a > 0 else (hi, lo)
            amin = None if amin is None or lo_term is None else amin + a * lo_term
            amax = None if amax is None or hi_term is None else amax + a * hi_term
        rl, ru = m.row_lower[i], m.row_upper[i]
        for sign, hit in (
            (-1, ru is not None and amin is not None and amin > ru),
            (1, rl is not None and amax is not None and amax < rl),
        ):
            if hit:
                y = [Fraction(0)] * m.num_rows
                y[i] = Fraction(sign)
                verdict = verify_farkas(m, y, f"interval_conflict:{m.row_ids[i]}")
                if verdict.verified:
                    return verdict
    return None


# --------------------------------------------------------------------------------------
# unboundedness
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnboundedVerdict:
    verified: bool
    reason: str
    anchor: tuple[Fraction, ...] = ()
    ray: tuple[Fraction, ...] = ()
    slope_min_form: Fraction | None = None


def verify_unbounded(
    m: CanonicalRangedLP, anchor: Sequence[Fraction], ray: Sequence[Fraction]
) -> UnboundedVerdict:
    n = m.num_variables
    if len(anchor) != n or len(ray) != n:
        return UnboundedVerdict(False, "dimension mismatch")
    if not any(d != 0 for d in ray):
        return UnboundedVerdict(False, "ray is zero")
    if not check_primal(m, anchor).exactly_feasible:
        return UnboundedVerdict(False, "anchor is not exactly feasible")
    for j, d in enumerate(ray):
        if (d > 0 and m.col_upper[j] is not None) or (d < 0 and m.col_lower[j] is not None):
            return UnboundedVerdict(False, f"ray leaves the bounds of column {m.variable_ids[j]}")
    for i, row in enumerate(m.rows):
        ad = sum((a * ray[j] for j, a in row), Fraction(0))
        if (ad > 0 and m.row_upper[i] is not None) or (ad < 0 and m.row_lower[i] is not None):
            return UnboundedVerdict(False, f"ray leaves the range of row {m.row_ids[i]}")
    slope = sum((m.cost[j] * ray[j] for j in range(n)), Fraction(0))
    if slope >= 0:
        return UnboundedVerdict(False, "ray does not improve the objective")
    return UnboundedVerdict(
        True, "exact anchor and improving recession ray", tuple(anchor), tuple(ray), slope
    )


def trivial_anchor_candidates(m: CanonicalRangedLP) -> list[list[Fraction]]:
    """Cheap exact anchor guesses: 0 clamped into the box, and the finite bounds."""
    zero = Fraction(0)
    clamp0: list[Fraction] = []
    for lo, hi in zip(m.col_lower, m.col_upper, strict=True):
        v = zero
        if lo is not None and v < lo:
            v = lo
        if hi is not None and v > hi:
            v = hi
        clamp0.append(v)
    lower = [lo if lo is not None else c for lo, c in zip(m.col_lower, clamp0, strict=True)]
    upper = [hi if hi is not None else c for hi, c in zip(m.col_upper, clamp0, strict=True)]
    return [clamp0, lower, upper]


_SMALL_DENOMINATORS: Final = (1, 10, 100, 1000, 10**4, 10**6, 10**9)


def anchor_candidates(
    m: CanonicalRangedLP, x_raw: Sequence[float] | None
) -> list[tuple[str, list[Fraction]]]:
    """Deterministic exact-anchor guesses; every one must still pass `check_primal` exactly.

    Sources: exact binary64 rationals, bounded-denominator reconstructions, the same with
    near-bound entries moved onto their finite bound, then box-clamped zero and the box corners.
    Moving a value onto a bound only makes a *candidate*; acceptance is exact feasibility.
    """
    out: list[tuple[str, list[Fraction]]] = []
    seen: set[tuple[Fraction, ...]] = set()

    def add(name: str, v: list[Fraction]) -> None:
        key = tuple(v)
        if key not in seen:
            seen.add(key)
            out.append((name, v))

    if x_raw is not None:
        exact = exact_vector(x_raw, m.num_variables, "primal_solution")
        add("solver_exact", exact)
        for cap in _SMALL_DENOMINATORS:
            add(f"solver_den{cap}", [x.limit_denominator(cap) for x in exact])
        tol = Fraction(1, 10**6)
        snapped: list[Fraction] = []
        for j, x in enumerate(exact):
            lo, hi = m.col_lower[j], m.col_upper[j]
            if lo is not None and abs(x - lo) <= tol:
                x = lo
            elif hi is not None and abs(x - hi) <= tol:
                x = hi
            snapped.append(x)
        add("solver_bound_snapped", snapped)
        for cap in _SMALL_DENOMINATORS:
            add(
                f"solver_snapped_den{cap}",
                [
                    s if (m.col_lower[j] == s or m.col_upper[j] == s) else s.limit_denominator(cap)
                    for j, s in enumerate(snapped)
                ],
            )
    for k, c in enumerate(trivial_anchor_candidates(m)):
        add(("box_clamped_zero", "box_lower", "box_upper")[k], c)
    return out


# --------------------------------------------------------------------------------------
# dual-candidate repair (any y is a valid weak-duality multiplier; repairs only help finiteness)
# --------------------------------------------------------------------------------------


def _wrong_sign_rows(m: CanonicalRangedLP, y: Sequence[Fraction]) -> list[int]:
    return [i for i in range(m.num_rows) if _h(m.row_lower[i], m.row_upper[i], y[i]) is None]


def _wrong_columns(m: CanonicalRangedLP, y: Sequence[Fraction]) -> tuple[list[int], list[Fraction]]:
    aty = _a_transpose_y(m, y)
    reduced = [m.cost[j] - aty[j] for j in range(m.num_variables)]
    bad = [
        j for j in range(m.num_variables) if _h(m.col_lower[j], m.col_upper[j], reduced[j]) is None
    ]
    return bad, reduced


def _solve_exact(
    rows_eq: list[dict[int, Fraction]], rhs: list[Fraction]
) -> dict[int, Fraction] | None:
    """Sparse exact Gauss elimination; a basic solution (free unknowns = 0) or None."""
    aug = [(dict(r), b) for r, b in zip(rows_eq, rhs, strict=True)]
    pivots: list[tuple[int, dict[int, Fraction], Fraction]] = []
    for r, b in aug:
        for pc, prow, pb in pivots:
            f = r.get(pc)
            if f:
                for k, v in prow.items():
                    nv = r.get(k, Fraction(0)) - f * v
                    if nv == 0:
                        r.pop(k, None)
                    else:
                        r[k] = nv
                b -= f * pb
        r = {k: v for k, v in r.items() if v != 0}
        if not r:
            if b != 0:
                return None
            continue
        pc = min(r)
        piv = r[pc]
        r = {k: v / piv for k, v in r.items()}
        b /= piv
        # eliminate this pivot column from earlier pivot rows (keeps reduced echelon form)
        for idx, (qc, qrow, qb) in enumerate(pivots):
            f = qrow.get(pc)
            if f:
                nq = dict(qrow)
                for k, v in r.items():
                    nv = nq.get(k, Fraction(0)) - f * v
                    if nv == 0:
                        nq.pop(k, None)
                    else:
                        nq[k] = nv
                pivots[idx] = (qc, nq, qb - f * b)
        pivots.append((pc, r, b))
    return {pc: pb for pc, _, pb in pivots}


_REPAIR_MAX_CELLS: Final = 40_000
_REPAIR_ROUNDS: Final = 8


def repair_dual(m: CanonicalRangedLP, y: Sequence[Fraction]) -> tuple[str, list[Fraction]] | None:
    """Try to turn a dual candidate with D(y) = -infinity into one with a finite exact bound.

    Rows whose multiplier sign points at an infinite row side are set to 0 and frozen; columns
    whose reduced cost points at an infinite column side are forced to reduced cost exactly 0 by
    solving A_V^T delta = -r_V over the unfrozen rows (exact, minimal-support). Repeats a bounded
    number of rounds. The result is only a candidate: the caller recomputes D exactly. Returns
    None when no repair is found or the system is too large (fail closed).
    """
    if len(y) != m.num_rows:
        return None
    cur = list(y)
    frozen: set[int] = set()
    forced: set[int] = set()
    for _ in range(_REPAIR_ROUNDS):
        for i in _wrong_sign_rows(m, cur):
            cur[i] = Fraction(0)
            frozen.add(i)
        bad, _ = _wrong_columns(m, cur)
        if not bad and not _wrong_sign_rows(m, cur):
            return ("dual_repaired", cur)
        forced.update(bad)
        if len(forced) * max(1, m.num_rows) > _REPAIR_MAX_CELLS:
            return None
        cols: dict[int, dict[int, Fraction]] = {j: {} for j in forced}
        for i, row in enumerate(m.rows):
            if i in frozen:
                continue
            for j, a in row:
                if j in cols:
                    cols[j][i] = cols[j].get(i, Fraction(0)) + a
        jlist = sorted(forced)
        sys_rows = [cols[j] for j in jlist]
        aty = _a_transpose_y(m, cur)
        # reduced cost after the step is r - A^T delta; require it to be exactly 0 on `forced`
        rhs = [m.cost[j] - aty[j] for j in jlist]
        sol = _solve_exact(sys_rows, rhs)
        if sol is None:
            return None
        for i, d in sol.items():
            cur[i] += d
    bad, _ = _wrong_columns(m, cur)
    if not bad and not _wrong_sign_rows(m, cur):
        return ("dual_repaired", cur)
    return None


# --------------------------------------------------------------------------------------
# numerical optimality gap
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GapVerdict:
    accepted: bool
    reason: str
    primal_original: float | None = None
    dual_original: float | None = None
    absolute_gap: float | None = None
    relative_gap: float | None = None


def evaluate_gap(
    m: CanonicalRangedLP,
    primal: PrimalCheck,
    y: Sequence[Fraction],
    policy: VerificationPolicy,
) -> GapVerdict:
    dual_min = dual_bound_min_form(m, y)
    if dual_min is None:
        return GapVerdict(False, "dual bound is -infinity (reduced cost on an unbounded side)")
    p = safe_float(to_original_sense(m, primal.objective_min_form))
    d = safe_float(to_original_sense(m, dual_min))
    signed = primal.objective_min_form - dual_min  # weak duality: >= 0 for an exactly feasible x
    gap = safe_float(abs(signed))
    if p is None or d is None or gap is None:
        return GapVerdict(False, "objective values are not representable as finite binary64")
    if signed < 0 and primal.exactly_feasible:
        # Impossible for an exactly feasible primal point: reject rather than trust abs().
        return GapVerdict(False, "negative gap contradicts weak duality", p, d, gap, None)
    scale = max(1.0, abs(p), abs(d))
    rel = gap / scale
    if gap <= policy.absolute_gap_tolerance or rel <= policy.relative_gap_tolerance:
        return GapVerdict(True, "gap within tolerance", p, d, gap, rel)
    return GapVerdict(False, "gap exceeds tolerance", p, d, gap, rel)


# --------------------------------------------------------------------------------------
# lowering validation (independent re-lowering)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LoweringCheck:
    valid: bool
    mismatches: tuple[str, ...]
    values_total: int = 0
    values_rounded: int = 0
    subnormal_count: int = 0
    max_abs_rounding_error: Fraction = field(default=Fraction(0))


def _relower(v: Fraction) -> float | None:
    try:
        f = float(v)
    except OverflowError:
        return None
    if not math.isfinite(f) or (v != 0 and f == 0.0):
        return None
    return f


def verify_lowering(m: CanonicalRangedLP, ir: LoweredLP, report: LoweringReport) -> LoweringCheck:
    """Re-lower the exact model independently and compare bit-for-bit with the IR and report."""
    bad: list[str] = []
    total = rounded = sub = 0
    max_err = Fraction(0)
    min_normal = 2.2250738585072014e-308

    def conv(v: Fraction, where: str) -> float | None:
        nonlocal total, rounded, sub, max_err
        f = _relower(v)
        if f is None:
            bad.append(f"{where}: not representable")
            return None
        total += 1
        err = abs(Fraction(f) - v)
        if err != 0:
            rounded += 1
            max_err = max(max_err, err)
        if f != 0.0 and abs(f) < min_normal:
            sub += 1
        return f

    def bnd(v: Fraction | None, inf: float, where: str) -> float | None:
        return inf if v is None else conv(v, where)

    def same(a: float | None, b: float, where: str) -> None:
        if a is None or (a != b and not (math.isnan(a) and math.isnan(b))):
            bad.append(f"{where}: IR value differs from independent lowering")
        elif a == 0.0 and math.copysign(1.0, b) < 0:
            bad.append(f"{where}: negative zero in IR")

    if ir.num_variables != m.num_variables or ir.num_rows != m.num_rows:
        bad.append("dimension mismatch")
        return LoweringCheck(False, tuple(bad))
    if report.model_sha256 != m.model_sha256:
        bad.append("report model hash differs from the canonical model")
    if report.ir_sha256 != ir.ir_sha256:
        bad.append("report IR hash differs from the IR")

    for j in range(m.num_variables):
        same(conv(m.cost[j], f"cost[{j}]"), ir.cost[j], f"cost[{j}]")
        same(bnd(m.col_lower[j], -math.inf, f"colLower[{j}]"), ir.col_lower[j], f"colLower[{j}]")
        same(bnd(m.col_upper[j], math.inf, f"colUpper[{j}]"), ir.col_upper[j], f"colUpper[{j}]")
    for i in range(m.num_rows):
        same(bnd(m.row_lower[i], -math.inf, f"rowLower[{i}]"), ir.row_lower[i], f"rowLower[{i}]")
        same(bnd(m.row_upper[i], math.inf, f"rowUpper[{i}]"), ir.row_upper[i], f"rowUpper[{i}]")
    starts, cols, vals = [0], [], []
    for row in m.rows:
        for j, a in row:
            cols.append(j)
            f = conv(a, "a")
            vals.append(f if f is not None else math.nan)
        starts.append(len(vals))
    if tuple(starts) != ir.row_start or tuple(cols) != ir.col_index:
        bad.append("CSR structure differs from independent lowering")
    elif len(vals) != len(ir.values) or any(a != b for a, b in zip(vals, ir.values, strict=True)):
        bad.append("CSR values differ from independent lowering")
    same(conv(m.offset, "offset"), ir.offset, "offset")

    if (total, rounded, sub, max_err) != (
        report.values_total,
        report.values_rounded,
        report.subnormal_count,
        report.max_abs_rounding_error,
    ):
        bad.append("report rounding summary differs from independent recomputation")
    return LoweringCheck(not bad, tuple(bad), total, rounded, sub, max_err)


def map_vector(ids: Sequence[str], values: Sequence[float]) -> Mapping[str, float]:
    """Pair ids with raw values (lengths must already have been checked)."""
    return dict(zip(ids, values, strict=True))
