"""Strict, immutable domain contracts for continuous production planning (LP only).

Numbers are exact ``Decimal`` values (binary floats are rejected so no silent rounding happens
at the business boundary). Units are explicit and never converted: a consumption record must
restate the product and resource units and they must match the declarations exactly.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

MAX_PRODUCTS: Final = 200
MAX_RESOURCES: Final = 200
MAX_CONSUMPTIONS: Final = 4000
MAX_ABS_VALUE: Final = Decimal(10) ** 12
MAX_DIGITS: Final = 17
MAX_INPUT_BYTES: Final = 1_000_000

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
_PLACEHOLDER_UNITS: Final = frozenset(
    {"", "unknown", "unspecified", "n/a", "na", "none", "?", "tbd"}
)

# keys that name features this slice refuses explicitly (clearer than "unknown field")
UNSUPPORTED_FEATURE_KEYS: Final = {
    "integer": "integer decisions",
    "is_integer": "integer decisions",
    "binary": "binary decisions",
    "setup": "machine setup decisions",
    "setup_cost": "machine setup decisions",
    "setup_time": "machine setup decisions",
    "batch_size": "lot sizing / integer batches",
    "lot_size": "lot sizing / integer batches",
    "sequence": "sequencing",
    "precedence": "sequencing",
    "schedule": "scheduling",
    "shifts": "scheduling",
    "nonlinear": "nonlinear production relationships",
    "piecewise": "nonlinear production relationships",
    "exponent": "nonlinear production relationships",
    "efficiency_curve": "nonlinear production relationships",
    "unit_conversion": "unit conversion",
}


class PlanningInputError(ValueError):
    """Invalid or unsupported business input; ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str, details: tuple[str, ...] = ()) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details


class ObjectiveMode(StrEnum):
    MAXIMIZE_MARGIN = "MAXIMIZE_MARGIN"
    MINIMIZE_COST = "MINIMIZE_COST"


class ResourceKind(StrEnum):
    MACHINE = "MACHINE"
    LABOR = "LABOR"
    MATERIAL = "MATERIAL"
    ENERGY = "ENERGY"
    OTHER = "OTHER"


def _exact(v: Any) -> Decimal:
    if isinstance(v, bool | float):
        raise ValueError("use an exact decimal (string, int or Decimal); binary floats are refused")
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except InvalidOperation as exc:
        raise ValueError("not a decimal number") from exc
    if not d.is_finite():
        raise ValueError("value must be finite")
    if abs(d) > MAX_ABS_VALUE:
        raise ValueError("magnitude exceeds 1e12")
    if len(d.as_tuple().digits) > MAX_DIGITS:
        raise ValueError(f"more than {MAX_DIGITS} significant digits")
    return d


Exact = Annotated[Decimal, BeforeValidator(_exact)]
NonNeg = Annotated[Decimal, BeforeValidator(_exact), Field(ge=0)]


def _ident(v: Any) -> str:
    if not isinstance(v, str) or not _ID.match(v):
        raise ValueError("id must match ^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
    return v


def _unit(v: Any) -> str:
    if not isinstance(v, str) or v != v.strip() or not 1 <= len(v) <= 32 or not v.isprintable():
        raise ValueError("unit must be a trimmed printable string of 1..32 characters")
    if v.lower() in _PLACEHOLDER_UNITS:
        raise ValueError("unit is unspecified")
    return v


Ident = Annotated[str, BeforeValidator(_ident)]
Unit = Annotated[str, BeforeValidator(_unit)]
Text = Annotated[str, Field(min_length=1, max_length=200)]

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=False)


class Product(BaseModel):
    model_config = _FROZEN
    id: Ident
    name: Text
    unit: Unit
    value_per_unit: Exact  # contribution margin (MAXIMIZE_MARGIN) or variable cost (MINIMIZE_COST)
    value_currency: Unit
    min_quantity: NonNeg = Decimal(0)  # demand commitment (lower bound on production)
    max_quantity: NonNeg | None = None  # demand / production cap

    @model_validator(mode="after")
    def _bounds(self) -> Product:
        if self.max_quantity is not None and self.min_quantity > self.max_quantity:
            raise ValueError(
                f"product {self.id}: min_quantity {self.min_quantity} exceeds "
                f"max_quantity {self.max_quantity}"
            )
        return self


class Resource(BaseModel):
    model_config = _FROZEN
    id: Ident
    name: Text
    kind: ResourceKind
    unit: Unit
    capacity: NonNeg


class Consumption(BaseModel):
    """Amount of one resource consumed per unit of one product (units restated, never converted)."""

    model_config = _FROZEN
    resource_id: Ident
    product_id: Ident
    amount_per_unit: NonNeg
    resource_unit: Unit
    product_unit: Unit


class PlanningMetadata(BaseModel):
    model_config = _FROZEN
    problem_id: Ident
    title: Text
    description: str = Field(default="", max_length=2000)
    period_label: str = Field(default="", max_length=100)


class PlanningProblem(BaseModel):
    model_config = _FROZEN
    metadata: PlanningMetadata
    mode: ObjectiveMode
    currency: Unit
    products: tuple[Product, ...]
    resources: tuple[Resource, ...]
    consumptions: tuple[Consumption, ...] = ()

    @model_validator(mode="after")
    def _consistency(self) -> PlanningProblem:
        if not self.products:
            raise ValueError("EMPTY_PRODUCTS: at least one product is required")
        if not self.resources:
            raise ValueError("EMPTY_RESOURCES: at least one resource is required")
        if len(self.products) > MAX_PRODUCTS or len(self.resources) > MAX_RESOURCES:
            raise ValueError("TOO_LARGE: product or resource count exceeds the supported limit")
        if len(self.consumptions) > MAX_CONSUMPTIONS:
            raise ValueError("TOO_LARGE: consumption record count exceeds the supported limit")
        for label, ids in (
            ("product", [p.id for p in self.products]),
            ("resource", [r.id for r in self.resources]),
        ):
            dup = [k for k, n in Counter(ids).items() if n > 1]
            if dup:
                raise ValueError(f"DUPLICATE_ID: duplicate {label} id(s) {sorted(dup)}")
        pmap = {p.id: p for p in self.products}
        rmap = {r.id: r for r in self.resources}
        seen: set[tuple[str, str]] = set()
        for c in self.consumptions:
            if c.product_id not in pmap:
                raise ValueError(f"UNKNOWN_REFERENCE: consumption refers to product {c.product_id}")
            if c.resource_id not in rmap:
                raise ValueError(
                    f"UNKNOWN_REFERENCE: consumption refers to resource {c.resource_id}"
                )
            key = (c.resource_id, c.product_id)
            if key in seen:
                raise ValueError(f"DUPLICATE_CONSUMPTION: {key}")
            seen.add(key)
            if c.product_unit != pmap[c.product_id].unit:
                raise ValueError(f"UNIT_MISMATCH: consumption {key} product unit differs")
            if c.resource_unit != rmap[c.resource_id].unit:
                raise ValueError(f"UNIT_MISMATCH: consumption {key} resource unit differs")
        for p in self.products:
            if p.value_currency != self.currency:
                raise ValueError(f"CURRENCY_MISMATCH: product {p.id} uses {p.value_currency}")
        if self.mode is ObjectiveMode.MINIMIZE_COST:
            if any(p.value_per_unit < 0 for p in self.products):
                raise ValueError("NEGATIVE_COST: MINIMIZE_COST requires non-negative unit costs")
            if not any(p.min_quantity > 0 for p in self.products):
                raise ValueError(
                    "NO_COMMITMENTS: MINIMIZE_COST needs at least one positive minimum commitment"
                )
        return self


_CODE = re.compile(r"^([A-Z_]{4,}):")


def _wrap(exc: ValidationError) -> PlanningInputError:
    lines: list[str] = []
    code = "INVALID_VALUE"
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = str(e["msg"]).removeprefix("Value error, ")
        lines.append(f"{loc}: {msg}" if loc else msg)
        if e["type"] == "missing":
            code = "MISSING_FIELD"
        elif e["type"] == "extra_forbidden":
            code = "UNSUPPORTED_OR_UNKNOWN_FIELD"
        else:
            m = _CODE.match(msg)
            if m and code == "INVALID_VALUE":
                code = m.group(1)
    return PlanningInputError(code, lines[0] if lines else "invalid input", tuple(lines))


def _scan_unsupported(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in UNSUPPORTED_FEATURE_KEYS:
                raise PlanningInputError(
                    "UNSUPPORTED_FEATURE",
                    f"{path}.{k}: {UNSUPPORTED_FEATURE_KEYS[k]} are not supported by the "
                    "continuous LP production-planning slice.",
                )
            _scan_unsupported(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan_unsupported(v, f"{path}[{i}]")


def validate_planning_dict(raw: dict[str, Any]) -> PlanningProblem:
    """Validate an untrusted mapping; raises PlanningInputError only."""
    _scan_unsupported(raw)
    try:
        return PlanningProblem.model_validate(raw)
    except ValidationError as exc:
        raise _wrap(exc) from exc


def _no_dups(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, v in pairs:
        if k in d:
            raise PlanningInputError("DUPLICATE_KEY", f"duplicate JSON key {k!r}")
        d[k] = v
    return d


def _bad_constant(name: str) -> Any:
    raise PlanningInputError("NON_FINITE_NUMBER", f"JSON constant {name} is not allowed")


def parse_planning_json(raw: bytes | str) -> PlanningProblem:
    """Strict JSON parse: exact decimals, no duplicate keys, no NaN/Infinity, size capped."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_INPUT_BYTES:
        raise PlanningInputError("INPUT_TOO_LARGE", f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        doc = json.loads(
            data.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=_bad_constant,
            object_pairs_hook=_no_dups,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningInputError("INVALID_JSON", str(exc)) from exc
    except RecursionError as exc:
        raise PlanningInputError("INVALID_JSON", "nesting too deep") from exc
    if not isinstance(doc, dict):
        raise PlanningInputError("INVALID_JSON", "root must be an object")
    return validate_planning_dict(doc)
