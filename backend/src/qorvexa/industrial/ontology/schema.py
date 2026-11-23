"""Versioned industrial ontology: concept and relation schemas, attribute validation, extensions.

The ontology is immutable data. Domain packs (maintenance, quality, energy, ...) are added with
``Ontology.extend`` and can never silently replace a core schema: re-declaring an id needs a
strictly higher version and a migration note. Units are validated with the Industrial Units
Engine only; this module implements no conversion.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from fractions import Fraction
from typing import Final

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.units import UnitError, exact_number, parse_unit

F = ConfigDict(frozen=True, extra="forbid")
ONTOLOGY_VERSION: Final = "qorvexa-ontology/1"
ID_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")
MAX_STR: Final = 300


class OntologyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ValueType(StrEnum):
    STRING = "STRING"
    INT = "INT"
    DECIMAL = "DECIMAL"  # exact decimal/fraction text, never float
    BOOL = "BOOL"
    ENUM = "ENUM"
    TIMESTAMP = "TIMESTAMP"
    QUANTITY = "QUANTITY"  # {"value": exact number, "unit": unit expression}
    REF = "REF"  # id of another entity in the same factory


class QuantityValue(BaseModel):
    model_config = F
    value: str
    unit: str


Value = str | int | bool | QuantityValue


class AttrSpec(BaseModel):
    model_config = F
    name: str
    vtype: ValueType
    required: bool = False
    enum: tuple[str, ...] = ()
    # QUANTITY only: the unit's dimension must equal that of this reference unit expression
    dimension_of: str | None = None
    ref_concept: str | None = None  # REF only
    minimum: str | None = None  # DECIMAL / QUANTITY lower bound (inclusive), exact


class ConceptSchema(BaseModel):
    model_config = F
    concept_id: str
    version: int = 1
    domain: str = "core"
    parent: str | None = None  # is-a: a Machine is-a Equipment
    attrs: tuple[AttrSpec, ...] = ()
    scope_required: tuple[str, ...] = ()  # subset of site_id / line_id / department
    deprecated: bool = False
    replaced_by: str | None = None
    migration_note: str = ""


class RelationSchema(BaseModel):
    model_config = F
    relation_id: str
    version: int = 1
    domain: str = "core"
    pairs: tuple[tuple[str, str], ...]  # allowed (source concept, target concept); is-a applies
    attrs: tuple[AttrSpec, ...] = ()
    max_out: int | None = None  # per source, among concurrently valid CONFIRMED edges
    max_in: int | None = None  # per target
    acyclic: bool = False
    deprecated: bool = False
    replaced_by: str | None = None
    migration_note: str = ""
    meaning: str = ""


class ExtensionPack(BaseModel):
    model_config = F
    domain: str
    concepts: tuple[ConceptSchema, ...] = ()
    relations: tuple[RelationSchema, ...] = ()


def _q(name: str, dim: str | None = None, *, required: bool = False) -> AttrSpec:
    return AttrSpec(name=name, vtype=ValueType.QUANTITY, dimension_of=dim, required=required)


def _s(name: str, *, required: bool = False) -> AttrSpec:
    return AttrSpec(name=name, vtype=ValueType.STRING, required=required)


def _e(name: str, *values: str, required: bool = False) -> AttrSpec:
    return AttrSpec(name=name, vtype=ValueType.ENUM, enum=values, required=required)


def _t(name: str) -> AttrSpec:
    return AttrSpec(name=name, vtype=ValueType.TIMESTAMP)


_SITE = ("site_id",)

_CORE_CONCEPTS: Final[tuple[ConceptSchema, ...]] = (
    ConceptSchema(concept_id="Organization"),
    ConceptSchema(concept_id="Facility", attrs=(_s("country"),)),
    ConceptSchema(concept_id="Site"),
    ConceptSchema(concept_id="ProductionLine", scope_required=_SITE),
    ConceptSchema(concept_id="WorkCenter", scope_required=_SITE),
    ConceptSchema(
        concept_id="Equipment",
        scope_required=_SITE,
        attrs=(_s("asset_id"), _s("manufacturer"), _s("model")),
    ),
    ConceptSchema(concept_id="Machine", parent="Equipment", scope_required=_SITE),
    ConceptSchema(concept_id="Tool", parent="Equipment", scope_required=_SITE),
    ConceptSchema(concept_id="Product", attrs=(_s("sku"), _q("margin_per_unit", "EGP/piece"))),
    ConceptSchema(concept_id="Material", attrs=(_s("sku"),)),
    ConceptSchema(concept_id="Component", parent="Material"),
    ConceptSchema(concept_id="Resource", attrs=(_e("kind", "TIME", "ENERGY", "LABOR", "OTHER"),)),
    ConceptSchema(
        concept_id="Capacity",
        attrs=(
            _q("amount", required=True),
            _e("basis", "AVAILABLE_TIME", "THROUGHPUT", "STORAGE", "OTHER", required=True),
            _s("period"),
        ),
    ),
    ConceptSchema(concept_id="Operation"),
    ConceptSchema(concept_id="Process"),
    ConceptSchema(concept_id="ProcessStage"),
    ConceptSchema(concept_id="Shift", attrs=(_t("starts"), _t("ends"))),
    ConceptSchema(concept_id="ProductionPeriod", attrs=(_t("starts"), _t("ends"))),
    ConceptSchema(concept_id="ProductionOrder", attrs=(_s("order_no"), _t("due"))),
    ConceptSchema(
        concept_id="Demand",
        attrs=(_q("quantity", "piece", required=True), _e("bound", "MIN", "MAX", required=True)),
    ),
    ConceptSchema(concept_id="Constraint", attrs=(_s("description"), _s("kind"))),
    ConceptSchema(concept_id="Dependency", attrs=(_s("description"), _s("kind"))),
    ConceptSchema(
        concept_id="Measurement",
        attrs=(
            _q("value", required=True),
            _s("quantity_kind"),
            _t("observed_at"),
            _e("temporal_class", "CURRENT", "HISTORICAL", required=True),
        ),
    ),
    ConceptSchema(
        concept_id="Observation",
        attrs=(_s("text"), _t("observed_at"), _e("temporal_class", "CURRENT", "HISTORICAL")),
    ),
)

_EQUIP = ("Equipment", "Machine", "Tool")
_ACT = ("Operation", "Process", "ProcessStage")
_LOC = ("Site", "ProductionLine", "WorkCenter")
_CAPABLE = ("Machine", "Equipment", "Resource", "WorkCenter", "ProductionLine")


def _pairs(sources: tuple[str, ...], targets: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((s, t) for s in sources for t in targets)


_CORE_RELATIONS: Final[tuple[RelationSchema, ...]] = (
    RelationSchema(
        relation_id="CONTAINS",
        acyclic=True,
        max_in=1,
        meaning="structural containment; the inverse (BELONGS_TO) is a traversal direction",
        pairs=(
            ("Organization", "Facility"),
            ("Facility", "Site"),
            ("Site", "ProductionLine"),
            ("Site", "WorkCenter"),
            ("ProductionLine", "WorkCenter"),
            ("ProductionLine", "Machine"),
            ("WorkCenter", "Machine"),
            ("WorkCenter", "Tool"),
            ("ProductionLine", "Equipment"),
            ("WorkCenter", "Equipment"),
            ("Process", "ProcessStage"),
            ("Process", "Operation"),
            ("ProcessStage", "Operation"),
        ),
    ),
    RelationSchema(
        relation_id="LOCATED_AT",
        max_out=1,
        pairs=_pairs((*_EQUIP, "Material", "Component"), _LOC),
    ),
    RelationSchema(
        relation_id="OPERATES",
        meaning="a shift runs a line or machine",
        pairs=(("Shift", "ProductionLine"), ("Shift", "Machine"), ("Shift", "WorkCenter")),
    ),
    RelationSchema(
        relation_id="PRODUCES",
        attrs=(_q("rate", "piece/h"),),
        pairs=_pairs(("Machine", "WorkCenter", *_ACT), ("Product", "Component")),
    ),
    RelationSchema(
        relation_id="CONSUMES",
        attrs=(_q("amount"),),
        pairs=_pairs(("Machine", "Product", *_ACT), ("Material", "Component")),
    ),
    RelationSchema(
        relation_id="REQUIRES",
        attrs=(_q("amount"),),
        pairs=_pairs(("Product", *_ACT), ("Resource", "Machine", "Tool")),
    ),
    RelationSchema(
        relation_id="DEPENDS_ON",
        acyclic=True,
        pairs=_pairs(
            ("Machine", "ProductionLine", "ProductionOrder", "Constraint", *_ACT),
            ("Machine", "ProductionLine", "Material", "Resource", *_ACT),
        ),
    ),
    RelationSchema(
        relation_id="ASSIGNED_TO",
        pairs=(
            ("ProductionOrder", "ProductionLine"),
            ("ProductionOrder", "Machine"),
            ("ProductionOrder", "Shift"),
            ("Operation", "Machine"),
            ("Operation", "WorkCenter"),
        ),
    ),
    RelationSchema(
        relation_id="SUPPLIES",
        attrs=(_q("rate"),),
        pairs=_pairs(("Material", "Component", "Resource"), ("Machine", "ProductionLine", *_ACT)),
    ),
    RelationSchema(relation_id="PRECEDES", acyclic=True, pairs=_pairs(_ACT, _ACT)),
    RelationSchema(relation_id="HAS_CAPACITY", pairs=_pairs(_CAPABLE, ("Capacity",))),
    RelationSchema(
        relation_id="HAS_CONSTRAINT",
        pairs=_pairs(
            ("Machine", "Resource", "Product", "ProductionLine", "Process", "Operation"),
            ("Constraint",),
        ),
    ),
    RelationSchema(
        relation_id="HAS_DEMAND", pairs=_pairs(("Product", "ProductionOrder"), ("Demand",))
    ),
    RelationSchema(
        relation_id="MEASURES",
        pairs=_pairs(
            ("Measurement", "Observation"),
            ("Machine", "Resource", "Product", "Process", "ProductionLine", "Capacity"),
        ),
    ),
)


class Ontology(BaseModel):
    model_config = F
    version: str = ONTOLOGY_VERSION
    concepts: tuple[ConceptSchema, ...]
    relations: tuple[RelationSchema, ...]

    # ---- lookups
    def concept(self, concept_id: str) -> ConceptSchema:
        for c in self.concepts:
            if c.concept_id == concept_id:
                return c
        raise OntologyError("UNKNOWN_CONCEPT", f"{concept_id!r} is not in the ontology")

    def relation(self, relation_id: str) -> RelationSchema:
        for r in self.relations:
            if r.relation_id == relation_id:
                return r
        raise OntologyError("UNKNOWN_RELATION", f"{relation_id!r} is not in the ontology")

    def has_concept(self, concept_id: str) -> bool:
        return any(c.concept_id == concept_id for c in self.concepts)

    def ancestors(self, concept_id: str) -> tuple[str, ...]:
        out, cur, seen = [concept_id], self.concept(concept_id).parent, {concept_id}
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.concept(cur).parent
        return tuple(out)

    def is_a(self, concept_id: str, ancestor: str) -> bool:
        return ancestor in self.ancestors(concept_id)

    def endpoint_ok(self, relation_id: str, source: str, target: str) -> bool:
        rel = self.relation(relation_id)
        sa, ta = self.ancestors(source), self.ancestors(target)
        return any(s in sa and t in ta for s, t in rel.pairs)

    def sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()

    # ---- extension / migration policy
    def extend(self, pack: ExtensionPack) -> Ontology:
        """Add a domain pack. Existing ids may only be re-declared at a strictly higher version
        with a migration note; unknown parents/endpoints and duplicate ids are rejected."""
        cons = {c.concept_id: c for c in self.concepts}
        rels = {r.relation_id: r for r in self.relations}
        for c in pack.concepts:
            if not ID_RE.match(c.concept_id):
                raise OntologyError("INVALID_SCHEMA", f"bad concept id {c.concept_id!r}")
            old = cons.get(c.concept_id)
            if old is not None and (c.version <= old.version or not c.migration_note.strip()):
                raise OntologyError(
                    "SCHEMA_OVERRIDE", f"{c.concept_id} needs a higher version and a migration note"
                )
            cons[c.concept_id] = c
        for r in pack.relations:
            if not ID_RE.match(r.relation_id):
                raise OntologyError("INVALID_SCHEMA", f"bad relation id {r.relation_id!r}")
            old_r = rels.get(r.relation_id)
            if old_r is not None and (r.version <= old_r.version or not r.migration_note.strip()):
                raise OntologyError(
                    "SCHEMA_OVERRIDE",
                    f"{r.relation_id} needs a higher version and a migration note",
                )
            rels[r.relation_id] = r
        for c in cons.values():
            if c.parent is not None and c.parent not in cons:
                raise OntologyError("INVALID_SCHEMA", f"{c.concept_id}: unknown parent {c.parent}")
        for r in rels.values():
            for s, t in r.pairs:
                if s not in cons or t not in cons:
                    raise OntologyError("INVALID_SCHEMA", f"{r.relation_id}: unknown endpoint")
        out = Ontology(
            concepts=tuple(sorted(cons.values(), key=lambda c: c.concept_id)),
            relations=tuple(sorted(rels.values(), key=lambda r: r.relation_id)),
        )
        for cid in cons:  # cycle in is-a would hang ancestors(); ancestors() guards, detect here
            chain = out.ancestors(cid)
            if len(set(chain)) != len(chain):
                raise OntologyError("INVALID_SCHEMA", f"is-a cycle at {cid}")
        return out

    # ---- attribute validation
    def validate_attrs(
        self, specs: tuple[AttrSpec, ...], attrs: dict[str, Value], *, owner: str
    ) -> dict[str, Value]:
        by = {s.name: s for s in specs}
        for k in attrs:
            if k not in by:
                raise OntologyError("UNKNOWN_ATTRIBUTE", f"{owner}: attribute {k!r} is not allowed")
        out: dict[str, Value] = {}
        for s in specs:
            if s.name not in attrs:
                if s.required:
                    raise OntologyError("MISSING_ATTRIBUTE", f"{owner}: {s.name!r} is required")
                continue
            out[s.name] = _check_value(owner, s, attrs[s.name])
        return out

    def concept_attrs(self, concept_id: str) -> tuple[AttrSpec, ...]:
        specs: dict[str, AttrSpec] = {}
        for cid in reversed(self.ancestors(concept_id)):  # parent first, child overrides
            for a in self.concept(cid).attrs:
                specs[a.name] = a
        return tuple(specs.values())


def _check_value(owner: str, s: AttrSpec, v: object) -> Value:
    def bad(msg: str) -> OntologyError:
        return OntologyError("INVALID_ATTRIBUTE", f"{owner}.{s.name}: {msg}")

    t = s.vtype
    if t is ValueType.STRING:
        if not isinstance(v, str) or not v.strip() or len(v) > MAX_STR:
            raise bad("non-empty string up to 300 characters expected")
        return v
    if t is ValueType.INT:
        if isinstance(v, bool) or not isinstance(v, int):
            raise bad("integer expected")
        return v
    if t is ValueType.BOOL:
        if not isinstance(v, bool):
            raise bad("boolean expected")
        return v
    if t is ValueType.ENUM:
        if not isinstance(v, str) or v not in s.enum:
            raise bad(f"one of {list(s.enum)} expected")
        return v
    if t is ValueType.TIMESTAMP:
        if not isinstance(v, str):
            raise bad("ISO-8601 timestamp expected")
        from datetime import datetime

        try:
            datetime.fromisoformat(v)
        except ValueError as exc:
            raise bad("ISO-8601 timestamp expected") from exc
        return v
    if t is ValueType.REF:
        if not isinstance(v, str) or not ID_RE.match(v):
            raise bad("entity id expected")
        return v
    if t is ValueType.DECIMAL:
        return _exact(s, v, bad)
    # QUANTITY
    if isinstance(v, dict):
        v = QuantityValue(**v)
    if not isinstance(v, QuantityValue):
        raise bad("quantity {value, unit} expected")
    exact = _exact_text(v.value, bad)
    if s.minimum is not None and exact < exact_number(s.minimum):
        raise bad(f"value below minimum {s.minimum}")
    try:
        unit = parse_unit(v.unit)
        if s.dimension_of is not None and unit.dimension != parse_unit(s.dimension_of).dimension:
            raise bad(
                f"unit {v.unit!r} has the wrong dimension (expected that of {s.dimension_of})"
            )
    except UnitError as exc:
        raise bad(f"unit rejected by the units engine: {exc}") from exc
    return QuantityValue(value=v.value.strip(), unit=v.unit.strip())


def _exact_text(text: str, bad: object) -> Fraction:
    try:
        return exact_number(text)
    except (UnitError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise OntologyError("INVALID_ATTRIBUTE", f"not an exact number: {text!r}") from exc


def _exact(s: AttrSpec, v: object, bad: object) -> str:
    if not isinstance(v, str):
        raise OntologyError("INVALID_ATTRIBUTE", f"{s.name}: exact number text expected")
    n = _exact_text(v, bad)
    if s.minimum is not None and n < exact_number(s.minimum):
        raise OntologyError("INVALID_ATTRIBUTE", f"{s.name}: below minimum {s.minimum}")
    return v.strip()


def core_ontology() -> Ontology:
    return Ontology(concepts=_CORE_CONCEPTS, relations=_CORE_RELATIONS)


__all__ = [
    "ID_RE",
    "ONTOLOGY_VERSION",
    "AttrSpec",
    "ConceptSchema",
    "ExtensionPack",
    "Ontology",
    "OntologyError",
    "QuantityValue",
    "RelationSchema",
    "Value",
    "ValueType",
    "core_ontology",
]
