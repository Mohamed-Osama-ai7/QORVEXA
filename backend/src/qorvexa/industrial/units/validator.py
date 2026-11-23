"""Dimensional validation of linear models: ``coefficient_unit x variable_unit = rhs_unit``.

Pure unit algebra on exact rationals. A model is CONSISTENT only if every term already is in the
right unit; same-dimension-different-scale terms are reported as NEEDS_EXPLICIT_CONVERSION with
the exact factor (never applied silently); anything else is INVALID.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from qorvexa.industrial.units.dimension import UnitError
from qorvexa.industrial.units.parser import Unit, parse_unit
from qorvexa.industrial.units.quantity import Compat, classify
from qorvexa.industrial.units.registry import UnitRegistry


class UnitStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    NEEDS_EXPLICIT_CONVERSION = "NEEDS_EXPLICIT_CONVERSION"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Term:
    variable: str
    coefficient_unit: str


@dataclass(frozen=True)
class ConstraintUnits:
    id: str
    terms: tuple[Term, ...]
    rhs_unit: str


@dataclass(frozen=True)
class ObjectiveUnits:
    terms: tuple[Term, ...]
    unit: str  # e.g. "EUR"


@dataclass(frozen=True)
class BoundUnits:
    id: str  # location label, e.g. "A.max_quantity"
    variable: str
    unit: str


@dataclass(frozen=True)
class UnitIssue:
    code: str
    location: str
    message: str
    factor: Fraction | None = None  # multiply the term's coefficient by this to reach the target


@dataclass(frozen=True)
class UnitReport:
    status: UnitStatus
    issues: tuple[UnitIssue, ...]
    sha256: str


def _report(issues: list[UnitIssue], subject: str = "") -> UnitReport:
    issues.sort(key=lambda i: (i.location, i.code, i.message))
    hard = any(i.code != "CONVERSION_REQUIRED" for i in issues)
    status = (
        UnitStatus.INVALID
        if hard
        else UnitStatus.NEEDS_EXPLICIT_CONVERSION
        if issues
        else UnitStatus.CONSISTENT
    )
    body = [
        [i.code, i.location, i.message, None if i.factor is None else str(i.factor)] for i in issues
    ]
    blob = json.dumps([status.value, body, subject], separators=(",", ":"), ensure_ascii=True)
    return UnitReport(status, tuple(issues), hashlib.sha256(blob.encode()).hexdigest())


class _Ctx:
    def __init__(self, reg: UnitRegistry) -> None:
        self.reg = reg
        self.issues: list[UnitIssue] = []
        self._cache: dict[str, Unit | None] = {}

    def unit(self, text: str, where: str) -> Unit | None:
        if text not in self._cache:
            try:
                self._cache[text] = parse_unit(text, self.reg)
            except UnitError as exc:
                self._cache[text] = None
                self.issues.append(UnitIssue(exc.code, where, f"{text!r}: {exc.message}"))
        return self._cache[text]

    def compare(self, got: Unit, want: Unit, where: str, what: str) -> None:
        c = classify(got, want)
        if c is Compat.SAME_UNIT:
            return
        msg = f"{what}: {got.symbol} [{got.dimension}] vs required {want.symbol} [{want.dimension}]"
        if c is Compat.CONVERTIBLE:
            self.issues.append(UnitIssue("CONVERSION_REQUIRED", where, msg, got.scale / want.scale))
        else:
            self.issues.append(UnitIssue(c.value, where, msg))

    def terms(
        self,
        terms: Sequence[Term],
        variables: Mapping[str, Unit | None],
        want: Unit | None,
        where: str,
        what: str,
    ) -> None:
        for t in terms:
            loc = f"{where}/term:{t.variable}"
            if t.variable not in variables:
                self.issues.append(UnitIssue("UNKNOWN_VARIABLE", loc, f"undeclared {t.variable!r}"))
                continue
            coeff = self.unit(t.coefficient_unit, loc)
            var = variables[t.variable]
            if coeff is None or var is None or want is None:
                continue
            self.compare(coeff.mul(var), want, loc, what)


def validate_linear_units(
    variables: Mapping[str, str],
    constraints: Sequence[ConstraintUnits],
    objective: ObjectiveUnits,
    bounds: Sequence[BoundUnits] = (),
    registry: UnitRegistry | None = None,
) -> UnitReport:
    reg = registry or UnitRegistry.default()
    ctx = _Ctx(reg)
    subject = repr((sorted(variables.items()), constraints, objective, bounds, reg.fingerprint()))
    vmap: dict[str, Unit | None] = {
        v: ctx.unit(u, f"variable:{v}") for v, u in sorted(variables.items())
    }
    for c in constraints:
        want = ctx.unit(c.rhs_unit, f"constraint:{c.id}/rhs")
        ctx.terms(c.terms, vmap, want, f"constraint:{c.id}", "coefficient x variable vs rhs")
    obj = ctx.unit(objective.unit, "objective/unit")
    ctx.terms(objective.terms, vmap, obj, "objective", "objective term vs objective unit")
    for b in bounds:
        loc = f"bound:{b.id}"
        bu = ctx.unit(b.unit, loc)
        if b.variable not in vmap:
            ctx.issues.append(UnitIssue("UNKNOWN_VARIABLE", loc, f"undeclared {b.variable!r}"))
        elif bu is not None and vmap[b.variable] is not None:
            ctx.compare(bu, vmap[b.variable], loc, "bound vs variable unit")  # type: ignore[arg-type]
    return _report(ctx.issues, hashlib.sha256(subject.encode()).hexdigest())
