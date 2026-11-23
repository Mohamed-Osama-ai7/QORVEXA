"""Unit-aware formulation: custom units, explicit conversions and dimensional analysis.

The ledger is the single source of truth: the unit registry is rebuilt from confirmed ``unitdef:``
facts, conversions are CONVERT ledger events whose exact factor is RECOMPUTED on every replay.
Nothing is converted unless a human approved that exact conversion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Context, Decimal
from fractions import Fraction
from typing import TYPE_CHECKING

from qorvexa.industrial.formulation.ledger import (
    ActorKind,
    Entry,
    FormulationError,
    Ledger,
    Op,
    _canon_decimal,
    _human,
)
from qorvexa.industrial.units import (
    Compat,
    PlanConsumption,
    PlanningUnitsInput,
    PlanProduct,
    PlanResource,
    UnitError,
    UnitRegistry,
    UnitStatus,
    classify,
    parse_unit,
    validate_planning_units,
)
from qorvexa.industrial.units.custom import CustomUnitDef, build_registry, parse_factor
from qorvexa.industrial.units.evidence import UnitEvidence

if TYPE_CHECKING:
    from qorvexa.industrial.formulation.analysis import ResolvedFact

MAX_SIG_DIGITS = 17
MAX_ABS = Decimal(10) ** 12


@dataclass(frozen=True)
class UIssue:
    code: str
    key: str
    message: str
    question: str = ""
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class Effective:
    units: dict[str, str]  # slot -> current unit text
    values: dict[str, str]  # numeric fact key -> value after approved conversions


def is_unit_metadata_key(key: str) -> bool:
    return key.endswith((":value_unit", ":bound_unit")) or (
        key.startswith("consumption:") and key.count(":") == 3
    )


def custom_defs(vals: Mapping[str, str]) -> list[CustomUnitDef]:
    out = []
    for k in sorted(vals):
        if k.startswith("unitdef:"):
            fac, expr = vals[k].split("|", 1)
            out.append(CustomUnitDef(k.split(":", 1)[1], expr, parse_factor(fac)))
    return out


def registry_from(vals: Mapping[str, str]) -> tuple[UnitRegistry, dict[str, UnitError]]:
    return build_registry(custom_defs(vals))


def _slot_keys(slot: str) -> tuple[str, list[str]] | None:
    """(unit fact key, numeric fact keys changed by converting this slot)."""
    p = slot.split(":")
    if p[0] == "consumption" and len(p) == 3:
        return f"consumption:{p[1]}:{p[2]}:unit", [f"consumption:{p[1]}:{p[2]}"]
    if p[0] == "resource" and len(p) == 2:
        return f"resource:{p[1]}:unit", [f"resource:{p[1]}:capacity"]
    if p[0] == "value" and len(p) == 2:
        return f"product:{p[1]}:value_unit", [f"product:{p[1]}:value_per_unit"]
    if p[0] == "bound" and len(p) == 2:
        return f"product:{p[1]}:bound_unit", [
            f"product:{p[1]}:min_quantity",
            f"product:{p[1]}:max_quantity",
        ]
    return None


def conversion_factor(src: str, dst: str, reg: UnitRegistry) -> Fraction:
    a, b = parse_unit(src, reg), parse_unit(dst, reg)
    c = classify(a, b)
    if c is Compat.SAME_UNIT:
        raise UnitError("CONVERSION_NOT_NEEDED", f"{src!r} and {dst!r} are already the same unit")
    if c is not Compat.CONVERTIBLE:
        raise UnitError(c.value, f"{src!r} cannot be converted to {dst!r}")
    return a.scale / b.scale


def scale_decimal(value: str, factor: Fraction) -> str:
    """value x factor as an exact decimal string, or CONVERSION_NOT_EXACT."""
    fr = Fraction(Decimal(value)) * factor
    if fr.denominator == 1:
        d = Decimal(fr.numerator)
    else:
        d = Context(prec=60).divide(Decimal(fr.numerator), Decimal(fr.denominator))
        if Fraction(d) != fr:
            raise UnitError("CONVERSION_NOT_EXACT", f"{value} x {factor} has no exact decimal form")
    if len(d.normalize().as_tuple().digits) > MAX_SIG_DIGITS or abs(d) > MAX_ABS:
        raise UnitError("CONVERSION_NOT_EXACT", "converted value exceeds 17 significant digits")
    return _canon_decimal(d)


def active_conversions(ledger: Ledger) -> list[Entry]:
    return [e for e in ledger.entries if e.op is Op.CONVERT and ledger.is_active(e.seq)]


def effective_state(
    vals: Mapping[str, str], conversions: Sequence[Entry], reg: UnitRegistry
) -> tuple[Effective, list[UIssue]]:
    units: dict[str, str] = {}
    values: dict[str, str] = {}
    for k, v in vals.items():
        if k.endswith(":value_unit"):
            units[f"value:{k.split(':')[1]}"] = v
        elif k.endswith(":bound_unit"):
            units[f"bound:{k.split(':')[1]}"] = v
        elif k.startswith("consumption:") and k.count(":") == 3:
            _, r, p, _u = k.split(":")
            units[f"consumption:{r}:{p}"] = v
        elif k.startswith("resource:") and k.endswith(":unit"):
            units[f"resource:{k.split(':')[1]}"] = v
        elif k.split(":")[0] in ("consumption", "product", "resource"):
            values[k] = v
    issues: list[UIssue] = []
    for e in conversions:
        slot = e.key or ""
        spec = _slot_keys(slot)
        if spec is None or slot not in units:
            issues.append(UIssue("CONVERSION_SLOT_UNKNOWN", slot, f"no unit slot {slot!r}"))
            continue
        if units[slot] != e.unit_from:
            issues.append(
                UIssue(
                    "CONVERSION_CHAIN_BROKEN",
                    slot,
                    f"approved conversion expects {e.unit_from!r} but the slot is {units[slot]!r}",
                )
            )
            continue
        try:
            factor = conversion_factor(units[slot], e.value or "", reg)
            new = {
                key: val if val == "NONE" else scale_decimal(val, factor)
                for key in spec[1]
                if (val := values.get(key)) is not None
            }
        except UnitError as exc:
            issues.append(UIssue(exc.code, slot, exc.message))
            continue
        values.update(new)
        units[slot] = e.value or ""
    return Effective(units, values), issues


def _resolved(ledger: Ledger) -> dict[str, str]:
    from qorvexa.industrial.formulation.analysis import resolve_facts

    facts, _ = resolve_facts(ledger)
    return {k: f.value for k, f in facts.items()}


def plan_conversion(ledger: Ledger, slot: str, to_unit: str) -> tuple[str, Fraction]:
    """(current unit, exact factor) for a new conversion, or FormulationError."""
    vals = _resolved(ledger)
    reg, errors = registry_from(vals)
    if errors:
        raise FormulationError("CONVERSION_BLOCKED", "custom unit definitions are not valid")
    eff, issues = effective_state(vals, active_conversions(ledger), reg)
    if issues:
        raise FormulationError(issues[0].code, issues[0].message)
    spec = _slot_keys(slot)
    if spec is None or slot not in eff.units:
        raise FormulationError("CONVERSION_SLOT_UNKNOWN", f"no unit slot {slot!r} is declared")
    cur = eff.units[slot]
    try:
        factor = conversion_factor(cur, to_unit, reg)
        for k in spec[1]:
            if k in eff.values and eff.values[k] != "NONE":
                scale_decimal(eff.values[k], factor)
    except UnitError as exc:
        raise FormulationError(exc.code, exc.message) from exc
    return cur, factor


def fmt_factor(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


def convert_units(
    ledger: Ledger, slot: str, to_unit: str, actor_id: str, rationale: str, at: str
) -> Ledger:
    """A human approves ONE exact conversion; recorded as an immutable CONVERT event."""
    actor = _human(actor_id)
    if len(rationale.strip()) < 10 or not at.strip():
        raise FormulationError("CONVERSION_INVALID", "rationale (10+ chars) and timestamp required")
    cur, factor = plan_conversion(ledger, slot, to_unit.strip())
    return ledger._append(
        op=Op.CONVERT,
        actor=actor,
        key=slot,
        value=to_unit.strip(),
        unit_from=cur,
        factor=fmt_factor(factor),
        at=at.strip(),
        rationale=rationale.strip(),
    )


def verify_conversion(prefix: Ledger, e: Entry) -> None:
    """Replay check: human, rationale, timestamp, and the factor/from-unit RECOMPUTED."""
    if e.actor.kind is not ActorKind.HUMAN or len(e.rationale.strip()) < 10 or not e.at:
        raise FormulationError("CONVERSION_INVALID", "conversion needs a human, rationale and time")
    cur, factor = plan_conversion(prefix, e.key or "", e.value or "")
    if e.unit_from != cur or e.factor != fmt_factor(factor):
        raise FormulationError(
            "CONVERSION_FORGED", "recorded factor/unit differ from recomputation"
        )


@dataclass(frozen=True)
class UnitAnalysis:
    issues: tuple[UIssue, ...]
    evidence: UnitEvidence | None
    eff: Effective | None


def _candidates(
    slot_to: Sequence[tuple[str, str]], eff: Effective, reg: UnitRegistry
) -> tuple[str, ...]:
    out = []
    for slot, to in slot_to:
        spec = _slot_keys(slot)
        if spec is None or slot not in eff.units:
            continue
        try:
            to_n = parse_unit(to, reg).symbol
            f = conversion_factor(eff.units[slot], to_n, reg)
        except UnitError:
            continue
        exact = True
        try:
            for k in spec[1]:
                if k in eff.values and eff.values[k] != "NONE":
                    scale_decimal(eff.values[k], f)
        except UnitError:
            exact = False
        out.append(f"{slot}|{to_n}|{fmt_factor(f)}|{'exact' if exact else 'inexact'}")
    return tuple(out)


def analyze_units(
    ledger: Ledger,
    facts: Mapping[str, ResolvedFact],
    products: Sequence[str],
    resources: Sequence[str],
) -> UnitAnalysis:
    vals = {k: f.value for k, f in facts.items()}
    reg, errors = registry_from(vals)
    if errors:
        return UnitAnalysis(
            tuple(
                UIssue(e.code, f"unitdef:{s}", e.message, "Retract or restate this definition.")
                for s, e in sorted(errors.items())
            ),
            None,
            None,
        )
    eff, bad = effective_state(vals, active_conversions(ledger), reg)
    if bad:
        return UnitAnalysis(tuple(bad), None, None)
    cur = vals["problem:currency"]
    punit = {p: vals[f"product:{p}:unit"] for p in products}
    inp = PlanningUnitsInput(
        cur,
        tuple(
            PlanProduct(p, punit[p], eff.units[f"value:{p}"], eff.units.get(f"bound:{p}"))
            for p in products
        ),
        tuple(PlanResource(r, eff.units[f"resource:{r}"]) for r in resources),
        tuple(
            PlanConsumption(r, p, eff.units[f"consumption:{r}:{p}"])
            for r in resources
            for p in products
        ),
    )
    report = validate_planning_units(inp, reg)
    issues: list[UIssue] = []
    for i in report.issues:
        loc = i.location
        if i.code != "CONVERSION_REQUIRED" or i.factor is None:
            issues.append(UIssue(i.code, loc, i.message, "Correct the stated units or values."))
            continue
        cands: list[tuple[str, str]] = []
        if loc.startswith("constraint:") and "/term:x_" in loc:
            r = loc[len("constraint:") : loc.index("/term:")]
            p = loc.split("/term:x_", 1)[1]
            cands = [
                (f"consumption:{r}:{p}", f"({eff.units[f'resource:{r}']})/({punit[p]})"),
                (
                    f"resource:{r}",
                    f"({eff.units[f'consumption:{r}:{p}']})*({punit[p]})",
                ),
            ]
        elif loc.startswith("objective/term:x_"):
            p = loc.split("x_", 1)[1]
            cands = [(f"value:{p}", f"{cur}/({punit[p]})")]
        elif loc.startswith("bound:"):
            p = loc[len("bound:") :].split(".")[0]
            cands = [(f"bound:{p}", punit[p])]
        issues.append(
            UIssue(
                "CONVERSION_REQUIRED",
                loc,
                f"{i.message}; compatible but differently scaled (exact factor "
                f"{fmt_factor(i.factor)}). Nothing is converted until a human approves one "
                "exact conversion.",
                "Which conversion do you approve?",
                _candidates(cands, eff, reg),
            )
        )
    if issues:
        return UnitAnalysis(tuple(issues), None, eff)
    defs = tuple((d.symbol, d.expression, fmt_factor(d.factor)) for d in custom_defs(vals))
    ev = UnitEvidence(inputs=inp, custom_units=defs, report_sha256=report.sha256)
    if report.status is not UnitStatus.CONSISTENT:  # pragma: no cover - guarded by issues above
        return UnitAnalysis(
            (UIssue("UNIT_VALIDATION_FAILED", "units", report.status.value),), None, eff
        )
    return UnitAnalysis((), ev, eff)
