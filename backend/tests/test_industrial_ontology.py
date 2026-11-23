# ruff: noqa: E501
"""Industrial Ontology & Context Engine: schemas, graph integrity, isolation, identity resolution,
temporal history, persistence, retrieval and the integration adapters.

All data is SYNTHETIC test data; it does not describe any real plant.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qorvexa.industrial.formulation import ActorKind, Ledger
from qorvexa.industrial.governance.authz import AuthorizationError, Principal, Role
from qorvexa.industrial.ontology import (
    Basis,
    ConceptSchema,
    ContextEngine,
    ContextQuery,
    Direction,
    EvidenceKind,
    EvidenceRef,
    ExtensionPack,
    FactoryOntology,
    IdentityResolver,
    InMemoryOntologyStore,
    OntologyError,
    Outcome,
    QuantityValue,
    RelationSchema,
    ResolveContext,
    RetrievalError,
    Scope,
    SqliteOntologyStore,
    Status,
    StoreError,
    convert_capacity,
    core_ontology,
    interpretation_context,
    link_report,
    planning_context,
    propose_terminology_for_entities,
    to_understanding_candidates,
    understanding_snapshot,
    verify_events,
    verify_factory,
)
from qorvexa.industrial.ontology.schema import AttrSpec, ValueType
from qorvexa.industrial.understanding import (
    InMemoryTerminologyStore,
    SourceDocument,
    TerminologyBook,
    UnderstandingSession,
    emit_proposals,
)

EV = (EvidenceRef(kind=EvidenceKind.HUMAN_STATEMENT, ref="synthetic-fixture"),)


def principal(pid: str, factory: str, *roles: Role, kind: ActorKind = ActorKind.HUMAN) -> Principal:
    return Principal(pid, kind, frozenset(roles), frozenset({factory}))


HUM1 = principal("ana", "F1", Role.PLANNER)
HUM2 = principal("omar", "F2", Role.PLANNER)
SVC1 = principal("extractor", "F1", Role.EXTRACTOR, kind=ActorKind.LLM)
SVC2 = principal("extractor2", "F2", Role.EXTRACTOR, kind=ActorKind.LLM)
REV1 = principal("rev", "F1", Role.TERMINOLOGY_REVIEWER)
REV2 = principal("rev2", "F2", Role.TERMINOLOGY_REVIEWER)


def make_clock():  # type: ignore[no-untyped-def]
    t = {"n": 0}

    def clock() -> str:
        t["n"] += 1
        return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=t["n"])).isoformat()

    return clock


def q(value: str, unit: str) -> QuantityValue:
    return QuantityValue(value=value, unit=unit)


class Fx:
    """A fully populated synthetic factory."""

    def __init__(self, fo: FactoryOntology, hum: Principal, svc: Principal) -> None:
        self.fo, self.hum, self.svc = fo, hum, svc

    def ent(self, concept: str, eid: str, name: str, **kw):  # type: ignore[no-untyped-def]
        return self.fo.create_entity(
            self.hum, concept, name, entity_id=eid, basis=Basis.HUMAN_CONFIRMED, evidence=EV, **kw
        )

    def rel(self, relation: str, src: str, tgt: str, **kw):  # type: ignore[no-untyped-def]
        return self.fo.create_relation(
            self.hum, relation, src, tgt, basis=Basis.HUMAN_CONFIRMED, evidence=EV, **kw
        )

    def state(self):  # type: ignore[no-untyped-def]
        return self.fo.view(self.hum)

    def engine(self) -> ContextEngine:
        return ContextEngine(self.state(), self.fo.ontology)


def build_factory(
    store, fid: str, hum: Principal, svc: Principal, *, nick: str, mach_id: str
) -> Fx:  # type: ignore[no-untyped-def]
    fo = FactoryOntology(store, fid, clock=make_clock())
    f = Fx(fo, hum, svc)
    f.ent("Organization", "org1", "ACME-SYNTHETIC")
    f.ent("Facility", "fac1", "Synthetic Works")
    f.ent("Site", "siteA", "Site A")
    f.ent("Site", "siteB", "Site B")
    f.rel("CONTAINS", "org1", "fac1")
    f.rel("CONTAINS", "fac1", "siteA")
    f.rel("CONTAINS", "fac1", "siteB")
    f.ent("ProductionLine", "L1", "Line 1", scope=Scope(site_id="siteA"))
    f.ent("ProductionLine", "L2", "Line 2", scope=Scope(site_id="siteA"))
    f.rel("CONTAINS", "siteA", "L1")
    f.rel("CONTAINS", "siteA", "L2")
    f.ent(
        "Machine", mach_id, "Cutter 100", scope=Scope(site_id="siteA"),
        attrs={"asset_id": "CNC-100", "manufacturer": "SynthCo"},
        aliases=(nick, "stacker", "الستاكر"),
    )  # fmt: skip
    f.ent("Machine", "M2", "Press 200", scope=Scope(site_id="siteA"), aliases=("big one",))
    f.ent("Machine", "M3", "Welder 300", scope=Scope(site_id="siteA"), aliases=("big one",))
    f.rel("CONTAINS", "L1", mach_id)
    f.rel("CONTAINS", "L1", "M2")
    f.rel("CONTAINS", "L2", "M3")
    f.ent("Product", "P1", "Widget", attrs={"margin_per_unit": q("50", "EGP/piece")})
    f.ent("Product", "P2", "Gadget")
    f.ent("Material", "MAT1", "Steel sheet")
    f.ent(
        "Capacity",
        "CAP1",
        "Cutter hours",
        attrs={"amount": q("160", "h"), "basis": "AVAILABLE_TIME"},
    )
    f.ent("Demand", "D1", "Widget demand", attrs={"quantity": q("50", "piece"), "bound": "MIN"})
    f.rel("PRODUCES", mach_id, "P1", attrs={"rate": q("40", "piece/h")})
    f.rel("PRODUCES", mach_id, "P2")
    f.rel("HAS_CAPACITY", mach_id, "CAP1")
    f.rel("REQUIRES", "P1", mach_id, attrs={"amount": q("2", "h/piece")})
    f.rel("CONSUMES", "P1", "MAT1", attrs={"amount": q("3", "kg/piece")})
    f.rel("HAS_DEMAND", "P1", "D1")
    for i in range(1, 7):
        f.ent("Operation", f"OP{i}", f"Op {i}")
    for i in range(1, 6):
        f.rel("PRECEDES", f"OP{i}", f"OP{i + 1}")
    return f


@pytest.fixture
def two(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = InMemoryOntologyStore()
    a = build_factory(store, "F1", HUM1, SVC1, nick="bet2af", mach_id="M1")
    b = build_factory(store, "F2", HUM2, SVC2, nick="bet2af", mach_id="K9")
    return store, a, b


# ============================================================================ schemas
class TestSchemas:
    def test_core_concepts_and_relations_present(self) -> None:
        o = core_ontology()
        for c in (
            "Facility",
            "Site",
            "ProductionLine",
            "WorkCenter",
            "Machine",
            "Equipment",
            "Tool",
            "Product",
            "Material",
            "Component",
            "Resource",
            "Capacity",
            "Operation",
            "Process",
            "ProcessStage",
            "Shift",
            "ProductionPeriod",
            "ProductionOrder",
            "Demand",
            "Constraint",
            "Dependency",
            "Measurement",
            "Observation",
        ):
            assert o.has_concept(c)
        for r in (
            "CONTAINS",
            "LOCATED_AT",
            "OPERATES",
            "PRODUCES",
            "CONSUMES",
            "REQUIRES",
            "DEPENDS_ON",
            "ASSIGNED_TO",
            "SUPPLIES",
            "PRECEDES",
            "HAS_CAPACITY",
            "HAS_CONSTRAINT",
        ):
            assert o.relation(r).relation_id == r
        assert o.is_a("Machine", "Equipment") and not o.is_a("Equipment", "Machine")
        assert o.endpoint_ok("PRODUCES", "Machine", "Product")
        assert not o.endpoint_ok("PRODUCES", "Product", "Machine")
        assert len(o.sha256()) == 64

    def test_extension_pack_without_changing_core(self) -> None:
        o = core_ontology()
        pack = ExtensionPack(
            domain="maintenance",
            concepts=(
                ConceptSchema(
                    concept_id="Sensor",
                    domain="maintenance",
                    parent="Equipment",
                    attrs=(AttrSpec(name="range", vtype=ValueType.STRING),),
                ),
            ),
            relations=(
                RelationSchema(
                    relation_id="MONITORS", domain="maintenance", pairs=(("Sensor", "Machine"),)
                ),
            ),
        )
        ext = o.extend(pack)
        assert ext.is_a("Sensor", "Equipment") and ext.endpoint_ok("MONITORS", "Sensor", "Machine")
        assert not o.has_concept("Sensor")  # the core object is immutable
        assert ext.sha256() != o.sha256()

    def test_schema_override_needs_higher_version_and_migration_note(self) -> None:
        o = core_ontology()
        with pytest.raises(OntologyError) as e:
            o.extend(ExtensionPack(domain="x", concepts=(ConceptSchema(concept_id="Machine"),)))
        assert e.value.code == "SCHEMA_OVERRIDE"
        ok = o.extend(
            ExtensionPack(
                domain="x",
                concepts=(
                    ConceptSchema(concept_id="Site", version=2, migration_note="adds nothing"),
                ),
            )
        )
        assert ok.concept("Site").version == 2
        with pytest.raises(OntologyError) as e2:
            o.extend(
                ExtensionPack(domain="x", concepts=(ConceptSchema(concept_id="Z", parent="Nope"),))
            )
        assert e2.value.code == "INVALID_SCHEMA"

    def test_deprecated_concept_rejected_for_new_entities(self) -> None:
        dep = core_ontology().extend(
            ExtensionPack(
                domain="m",
                concepts=(
                    ConceptSchema(
                        concept_id="Tool",
                        version=2,
                        parent="Equipment",
                        deprecated=True,
                        replaced_by="Machine",
                        scope_required=("site_id",),
                        migration_note="use Machine",
                    ),
                ),
            )
        )
        fo = FactoryOntology(InMemoryOntologyStore(), "F1", dep, clock=make_clock())
        f = Fx(fo, HUM1, SVC1)
        f.ent("Site", "s1", "S")
        with pytest.raises(OntologyError) as e:
            f.ent("Tool", "t1", "T", scope=Scope(site_id="s1"))
        assert e.value.code == "CONCEPT_DEPRECATED"


# ============================================================================ creation & validation
class TestValidation:
    def test_valid_creation_and_provenance(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        m = a.state().entities["M1"]
        assert m.authoritative and m.basis is Basis.HUMAN_CONFIRMED and m.created_by == "ana"
        assert m.evidence == EV and m.data.attrs["asset_id"] == "CNC-100"
        assert a.state().relations_of("M1", Direction.IN)[0].data.relation == "CONTAINS"

    def test_invalid_concept_and_attributes(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        cases = [
            (lambda: a.ent("Spaceship", "x1", "X"), "UNKNOWN_CONCEPT"),
            (lambda: a.ent("Product", "x2", "X", attrs={"color": "red"}), "UNKNOWN_ATTRIBUTE"),
            (
                lambda: a.ent("Capacity", "x3", "X", attrs={"amount": q("1", "h")}),
                "MISSING_ATTRIBUTE",
            ),
            (
                lambda: a.ent("Product", "x4", "X", attrs={"margin_per_unit": q("5", "kg")}),
                "INVALID_ATTRIBUTE",
            ),
            (
                lambda: a.ent(
                    "Product", "x5", "X", attrs={"margin_per_unit": q("abc", "EGP/piece")}
                ),
                "INVALID_ATTRIBUTE",
            ),
            (
                lambda: a.ent(
                    "Product", "x6", "X", attrs={"margin_per_unit": q("5", "furlongs/fortnight")}
                ),
                "INVALID_ATTRIBUTE",
            ),
            (
                lambda: a.ent(
                    "Capacity", "x7", "X", attrs={"amount": q("1", "h"), "basis": "WISHFUL"}
                ),
                "INVALID_ATTRIBUTE",
            ),
            (lambda: a.ent("Machine", "x8", "X"), "MISSING_SCOPE"),
            (lambda: a.ent("Machine", "x9", "X", scope=Scope(site_id="nope")), "DANGLING_SCOPE"),
            (lambda: a.ent("Product", "x10", "N" * 301), "INVALID_NAME"),
            (lambda: a.ent("Product", "bad id!", "X"), "INVALID_ID"),
            (lambda: a.ent("Product", "P1", "dup"), "DUPLICATE_ENTITY"),
            (
                lambda: a.ent(
                    "Product", "x11", "X", valid_from="2026-02-01", valid_until="2026-01-01"
                ),
                "INVALID_VALIDITY",
            ),
        ]
        for fn, code in cases:
            with pytest.raises(OntologyError) as e:
                fn()
            assert e.value.code == code, (code, e.value.code)

    def test_authoritative_assertion_needs_evidence(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        with pytest.raises(OntologyError) as e:
            a.fo.create_entity(
                a.hum, "Product", "NoEv", entity_id="P9", basis=Basis.HUMAN_CONFIRMED
            )
        assert e.value.code == "EVIDENCE_REQUIRED"
        with pytest.raises(OntologyError) as e2:
            a.fo.create_entity(a.hum, "Product", "TooMany", basis=Basis.PROPOSAL, evidence=EV * 21)
        assert e2.value.code == "TOO_MANY_EVIDENCE"

    def test_invalid_relation_endpoints(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        for args, code in (
            (("PRODUCES", "P1", "M1"), "ENDPOINT_INCOMPATIBLE"),
            (("CONTAINS", "M1", "L1"), "ENDPOINT_INCOMPATIBLE"),
            (("PRODUCES", "M1", "ghost"), "ENDPOINT_NOT_FOUND"),
            (("FLIES_TO", "M1", "P1"), "UNKNOWN_RELATION"),
        ):
            with pytest.raises(OntologyError) as e:
                a.rel(*args)
            assert e.value.code == code
        with pytest.raises(OntologyError) as e2:  # attribute not defined for this relation
            a.rel("HAS_CAPACITY", "M2", "CAP1", attrs={"weight": "1"})
        assert e2.value.code == "UNKNOWN_ATTRIBUTE"

    def test_duplicate_and_orphan_detection(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        with pytest.raises(OntologyError) as e:
            a.rel("PRODUCES", "M1", "P1")
        assert e.value.code == "DUPLICATE_RELATION"
        # orphan relations cannot be created through the API, and integrity flags a forged one
        events = a.fo.events(a.hum)
        report = verify_factory("F1", events, a.fo.ontology)
        assert report.ok, report
        forged = [ev for ev in events if ev.object_id != "P2" or ev.kind.value != "ENTITY"]
        assert any(
            i.code == "ORPHAN_RELATION"
            for i in verify_factory("F1", _rechain(forged), a.fo.ontology).graph_issues
        )

    def test_confirmed_relation_needs_confirmed_endpoints(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "Maybe", entity_id="P7")  # proposal
        with pytest.raises(OntologyError) as e:
            a.rel("PRODUCES", "M2", "P7")
        assert e.value.code == "ENDPOINT_NOT_CONFIRMED"
        # a proposed relation on a proposed entity is allowed but stays a proposal
        r = a.fo.create_relation(a.svc, "PRODUCES", "M2", "P7")
        assert r.status is Status.PROPOSED and not r.authoritative

    def test_conflicting_relationships_cardinality_and_cycles(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.rel("LOCATED_AT", "M2", "siteA")
        with pytest.raises(OntologyError) as e:  # a machine has one location at a time
            a.rel("LOCATED_AT", "M2", "siteB")
        assert e.value.code == "CARDINALITY_VIOLATION"
        with pytest.raises(OntologyError) as e2:  # one container per machine
            a.rel("CONTAINS", "L2", "M1")
        assert e2.value.code == "CARDINALITY_VIOLATION"
        with pytest.raises(OntologyError) as e3:  # PRECEDES is acyclic
            a.rel("PRECEDES", "OP6", "OP1")
        assert e3.value.code == "CYCLE_DETECTED"
        a.rel("DEPENDS_ON", "OP2", "OP1")
        with pytest.raises(OntologyError) as e4:
            a.rel("DEPENDS_ON", "OP1", "OP2")
        assert e4.value.code == "CYCLE_DETECTED"
        with pytest.raises(OntologyError) as e5:
            a.rel("DEPENDS_ON", "OP3", "OP3")
        assert e5.value.code == "CYCLE_DETECTED"

    def test_non_overlapping_validity_windows_are_not_a_conflict(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.rel(
            "LOCATED_AT",
            "M3",
            "siteA",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until="2026-06-01T00:00:00+00:00",
        )
        a.rel("LOCATED_AT", "M3", "siteB", valid_from="2026-06-01T00:00:00+00:00")
        eng = a.engine()
        for at, site in (
            ("2026-03-01T00:00:00+00:00", "siteA"),
            ("2026-08-01T00:00:00+00:00", "siteB"),
        ):
            res = eng.retrieve(
                ContextQuery(
                    roots=("M3",),
                    relations=("LOCATED_AT",),
                    direction=Direction.OUT,
                    valid_at=at,
                    max_depth=1,
                )
            )
            assert [n.entity_id for n in res.nodes if n.concept == "Site"] == [site]

    def test_unit_attributes_use_the_units_engine(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        cap = a.state().entities["CAP1"].data.attrs["amount"]
        assert isinstance(cap, QuantityValue)
        assert convert_capacity(cap, "min") == q("9600", "min")
        with pytest.raises(Exception) as e:  # currency vs time: refused by the units engine
            convert_capacity(q("5", "EGP"), "h")
        assert "MISMATCH" in str(e.value).upper() or "DIMENSION" in str(e.value).upper()


def _rechain(events):  # type: ignore[no-untyped-def]
    """Re-number and re-hash a subset of events (simulates a forged but self-consistent log)."""
    out, prev = [], "0" * 64
    for i, ev in enumerate(events, start=1):
        ev = ev.model_copy(update={"seq": i, "prev_sha": prev, "sha": ""})
        ev = ev.model_copy(update={"sha": ev.body_sha256()})
        out.append(ev)
        prev = ev.sha
    return out


# ============================================================================ authorization / concurrency
class TestGovernance:
    def test_llm_can_only_propose(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        p = a.fo.create_entity(a.svc, "Product", "Idea", entity_id="P5")
        assert p.status is Status.PROPOSED and p.basis is Basis.PROPOSAL
        for basis in (
            Basis.HUMAN_CONFIRMED,
            Basis.IMPORTED_VERIFIED,
            Basis.ASSUMPTION,
            Basis.HYPOTHETICAL,
        ):
            with pytest.raises(AuthorizationError) as e:
                a.fo.create_entity(a.svc, "Product", "Forced", basis=basis, evidence=EV)
            assert e.value.code == "HUMAN_APPROVAL_REQUIRED"
        with pytest.raises(AuthorizationError):
            a.fo.confirm(a.svc, "P5", expected_version=1, evidence=EV)
        with pytest.raises(AuthorizationError):
            a.fo.update(a.svc, "P1", expected_version=1, name="Hijacked", evidence=EV)
        with pytest.raises(AuthorizationError):
            a.fo.retire(a.svc, "P1", expected_version=1, note="x")

    def test_a_service_cannot_hold_a_human_role(self) -> None:
        with pytest.raises(AuthorizationError) as e:
            Principal("bot", ActorKind.LLM, frozenset({Role.PLANNER}), frozenset({"F1"}))
        assert e.value.code == "INVALID_PRINCIPAL"

    def test_human_confirmation_is_the_only_path_to_authority(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "Idea", entity_id="P5", evidence=EV)
        assert a.engine().retrieve(ContextQuery(roots=("P1",), concepts=("Product",))).nodes
        assert "P5" not in {n.entity_id for n in a.engine().facts_for_problem(["P1", "P2"]).nodes}
        done = a.fo.confirm(a.hum, "P5", expected_version=1, note="checked with the planner")
        assert done.status is Status.CONFIRMED and done.version == 2
        with pytest.raises(OntologyError) as e:
            a.fo.confirm(a.hum, "P5", expected_version=2)
        assert e.value.code == "NOT_CONFIRMABLE"

    def test_confirm_needs_evidence_and_revalidates(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "NoProof", entity_id="P6")
        with pytest.raises(OntologyError) as e:
            a.fo.confirm(a.hum, "P6", expected_version=1)
        assert e.value.code == "EVIDENCE_REQUIRED"
        # a proposed cycle cannot be confirmed
        rel = a.fo.create_relation(a.svc, "PRECEDES", "OP6", "OP1", evidence=EV)
        with pytest.raises(OntologyError) as e2:
            a.fo.confirm(a.hum, rel.relation_id, expected_version=1)
        assert e2.value.code == "CYCLE_DETECTED"

    def test_reject_and_retire_rules(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "Junk", entity_id="P8")
        with pytest.raises(OntologyError) as e:
            a.fo.reject(a.hum, "P8", expected_version=1, note=" ")
        assert e.value.code == "NOTE_REQUIRED"
        assert (
            a.fo.reject(a.hum, "P8", expected_version=1, note="not real").status is Status.REJECTED
        )
        with pytest.raises(OntologyError) as e3:
            a.fo.retire(a.hum, "M1", expected_version=a.state().entities["M1"].version, note="gone")
        assert e3.value.code == "HAS_LIVE_RELATIONS"

    def test_revision_and_version_conflicts(self, two) -> None:  # type: ignore[no-untyped-def]
        store, a, _ = two
        v = a.state().entities["P1"].version
        a.fo.update(a.hum, "P1", expected_version=v, name="Widget v2", evidence=EV)
        with pytest.raises(StoreError) as e:  # stale object version
            a.fo.update(a.hum, "P1", expected_version=v, name="Widget v3", evidence=EV)
        assert e.value.code == "VERSION_CONFLICT"
        with pytest.raises(StoreError) as e2:  # stale factory revision
            a.fo.create_entity(
                a.hum,
                "Product",
                "X",
                basis=Basis.HUMAN_CONFIRMED,
                evidence=EV,
                entity_id="P20",
                expected_revision=a.fo.revision - 1,
            )
        assert e2.value.code == "REVISION_CONFLICT"
        with pytest.raises(StoreError) as e3:  # the store itself refuses a stale append
            store.append("F1", [], expected_revision=1)
        assert e3.value.code == "REVISION_CONFLICT"

    def test_second_writer_on_the_same_store_is_seen(self, two) -> None:  # type: ignore[no-untyped-def]
        store, a, _ = two
        other = FactoryOntology(store, "F1", clock=make_clock())
        other.update(
            HUM1,
            "P1",
            expected_version=a.state().entities["P1"].version,
            name="Renamed",
            evidence=EV,
        )
        # the first service has a stale cache but still sees the new version and rejects a stale write
        with pytest.raises(StoreError) as e:
            a.fo.update(a.hum, "P1", expected_version=1, name="Old view", evidence=EV)
        assert e.value.code == "VERSION_CONFLICT"
        assert a.state().entities["P1"].data.name == "Renamed"

    def test_idempotent_create(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        r1 = a.fo.create_entity(a.svc, "Product", "Idem", idempotency_key="k1")
        rev = a.fo.revision
        r2 = a.fo.create_entity(a.svc, "Product", "Idem", idempotency_key="k1")
        assert r1.entity_id == r2.entity_id and a.fo.revision == rev
        with pytest.raises(StoreError) as e:
            a.fo.create_entity(a.svc, "Product", "Different", idempotency_key="k1")
        assert e.value.code == "IDEMPOTENCY_CONFLICT"

    def test_disputes_stay_visible_until_a_human_resolves_them(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        rid = next(
            r.relation_id for r in a.state().relations.values() if r.data.relation == "HAS_CAPACITY"
        )
        d = a.fo.dispute(a.svc, rid, "operator says 120 h, not 160 h", evidence=EV)
        assert a.state().relations[rid].disputed and d.open
        eng = a.engine()
        assert rid not in {e.relation_id for e in eng.machine_context("M1").edges}
        assert rid in {
            e.relation_id
            for e in eng.retrieve(
                ContextQuery(roots=("M1",), include_disputed=True, relations=("HAS_CAPACITY",))
            ).edges
        }
        assert rid in eng.unresolved().disputed and d.dispute_id in eng.unresolved().open_disputes
        with pytest.raises(AuthorizationError):
            a.fo.resolve_dispute(a.svc, d.dispute_id, "fine")
        a.fo.resolve_dispute(a.hum, d.dispute_id, "re-measured: 160 h is right")
        assert not a.state().relations[rid].disputed
        with pytest.raises(OntologyError):
            a.fo.resolve_dispute(a.hum, d.dispute_id, "again")


# ============================================================================ factory isolation
class TestIsolation:
    def test_factory_a_cannot_read_or_write_factory_b(self, two) -> None:  # type: ignore[no-untyped-def]
        _store, a, b = two
        with pytest.raises(AuthorizationError) as e:
            b.fo.view(a.hum)
        assert e.value.code == "FACTORY_FORBIDDEN"
        for call in (
            lambda: b.fo.create_entity(
                a.hum, "Product", "Spy", basis=Basis.HUMAN_CONFIRMED, evidence=EV
            ),
            lambda: b.fo.create_entity(a.svc, "Product", "Spy"),
            lambda: b.fo.update(a.hum, "P1", expected_version=1, name="x", evidence=EV),
            lambda: b.fo.events(a.hum),
            lambda: b.fo.dispute(a.svc, "P1", "x"),
        ):
            with pytest.raises(AuthorizationError):
                call()
        unscoped = Principal("u", ActorKind.HUMAN, frozenset({Role.PLANNER}))
        with pytest.raises(AuthorizationError):
            a.fo.view(unscoped)

    def test_ids_do_not_leak_across_factories(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, b = two
        assert "K9" in b.state().entities and "K9" not in a.state().entities
        with pytest.raises(RetrievalError) as e:
            b.engine().retrieve(ContextQuery(roots=("M1",)))  # M1 exists only in F1
        assert e.value.code == "UNKNOWN_ROOT"
        with pytest.raises(OntologyError) as e2:
            b.fo.update(b.hum, "M1", expected_version=1, name="x", evidence=EV)
        assert e2.value.code == "UNKNOWN_OBJECT"

    def test_same_nickname_resolves_to_different_machines_per_factory(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, b = two
        ra = IdentityResolver(a.state(), a.fo.ontology).resolve("bet2af")
        rb = IdentityResolver(b.state(), b.fo.ontology).resolve("bet2af")
        assert (ra.outcome, ra.entity_id) == (Outcome.RESOLVED, "M1")
        assert (rb.outcome, rb.entity_id) == (Outcome.RESOLVED, "K9")

    def test_terminology_of_another_factory_is_ignored(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        other = TerminologyBook(InMemoryTerminologyStore(), "F2")
        t = other.propose(REV2, "حاجة غريبة", "x", entity_type="MACHINE", canonical_id="M2")
        other.confirm(REV2, t.term_id)
        res = IdentityResolver(a.state(), a.fo.ontology, other.entries()).resolve("حاجة غريبة")
        assert res.outcome is Outcome.UNRESOLVED

    def test_events_and_integrity_are_per_factory(self, two) -> None:  # type: ignore[no-untyped-def]
        store, _a, _b = two
        assert {e.factory_id for e in store.events("F1")} == {"F1"}
        assert verify_factory("F1", store.events("F1"), core_ontology()).ok
        assert any("belongs to factory" in m for m in verify_events("F1", store.events("F2")))
        with pytest.raises(StoreError) as e:
            store.append("F1", [store.events("F2")[0]], store.revision("F1"))
        assert e.value.code in {"FACTORY_MISMATCH", "BAD_SEQUENCE"}


# ============================================================================ identity resolution
class TestResolution:
    def test_official_id_name_alias_and_language_variants(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        r = IdentityResolver(a.state(), a.fo.ontology)
        for surface in (
            "M1",
            "CNC-100",
            "cnc-100",
            "Cutter 100",
            "stacker",
            "STACKER",
            "الستاكر",
            "bet2af",
        ):
            res = r.resolve(surface)
            assert (res.outcome, res.entity_id) == (Outcome.RESOLVED, "M1"), surface

    def test_shared_nickname_is_ambiguous_until_context_separates_it(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        r = IdentityResolver(a.state(), a.fo.ontology)
        res = r.resolve("big one")
        assert res.outcome is Outcome.AMBIGUOUS and res.entity_id is None
        assert {c.entity_id for c in res.candidates} == {"M2", "M3"}
        by_line = r.resolve("big one", ResolveContext(line_id="L2"))
        assert (by_line.outcome, by_line.entity_id) == (Outcome.RESOLVED, "M3")
        assert [c.entity_id for c in by_line.excluded] == ["M2"] and by_line.excluded[
            0
        ].excluded.startswith("OTHER_LINE")
        # an already-identified neighbour is evidence, and the evidence is reported
        by_rel = r.resolve("big one", ResolveContext(related_entity_ids=("L1",)))
        assert by_rel.entity_id == "M2" and any(
            x.startswith("relation_support") for x in by_rel.candidates[0].evidence
        )
        # a line that fits neither machine leaves nothing to choose
        none = r.resolve("big one", ResolveContext(line_id="L9"))
        assert none.outcome is Outcome.UNRESOLVED and none.entity_id is None
        assert {c.entity_id for c in none.excluded} == {"M2", "M3"}

    def test_unknown_and_proposed_only_are_unresolved(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(
            a.svc,
            "Machine",
            "Phantom Mill",
            entity_id="M9",
            scope=Scope(site_id="siteA"),
            aliases=("phantom",),
        )
        r = IdentityResolver(a.state(), a.fo.ontology)
        assert r.resolve("nothing like it").outcome is Outcome.UNRESOLVED
        ph = r.resolve("Phantom Mill")
        assert ph.outcome is Outcome.UNRESOLVED and ph.entity_id is None
        assert ph.excluded[0].excluded.startswith("NOT_CONFIRMED")
        assert r.resolve("   ").outcome is Outcome.UNRESOLVED

    def test_concept_filter(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        r = IdentityResolver(a.state(), a.fo.ontology)
        assert r.resolve("Widget", ResolveContext(concepts=("Product",))).entity_id == "P1"
        res = r.resolve("Widget", ResolveContext(concepts=("Machine",)))
        assert res.outcome is Outcome.UNRESOLVED and res.excluded[0].excluded.startswith(
            "CONCEPT_MISMATCH"
        )

    def test_arabic_and_english_aliases_through_terminology_adapter(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        t = book.propose(
            REV1, "القطاعة", "machine", entity_type="MACHINE", canonical_id="M1", language="ar"
        )
        # unconfirmed terminology is not used
        r = IdentityResolver(a.state(), a.fo.ontology, book.entries()).resolve("القطاعة")
        assert r.outcome is Outcome.UNRESOLVED
        book.confirm(REV1, t.term_id)
        r = IdentityResolver(a.state(), a.fo.ontology, book.entries()).resolve("القطاعة")
        assert (r.outcome, r.entity_id, r.term_ids) == (Outcome.RESOLVED, "M1", (t.term_id,))

    def test_term_pointing_to_a_missing_entity_is_preserved_unresolved(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        t = book.propose(REV1, "الشبح", "machine", entity_type="MACHINE", canonical_id="M404")
        book.confirm(REV1, t.term_id)
        r = IdentityResolver(a.state(), a.fo.ontology, book.entries()).resolve("الشبح")
        assert r.outcome is Outcome.UNRESOLVED and "TERM_TARGET_MISSING" in r.excluded[0].excluded

    def test_terminology_and_ontology_disagreeing_is_conflicted(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        t = book.propose(REV1, "stacker", "machine", entity_type="MACHINE", canonical_id="M2")
        book.confirm(REV1, t.term_id)
        r = IdentityResolver(a.state(), a.fo.ontology, book.entries()).resolve("stacker")
        assert r.outcome is Outcome.CONFLICTED and {c.entity_id for c in r.candidates} == {
            "M1",
            "M2",
        }

    def test_declared_terminology_ambiguity_is_never_guessed(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        from qorvexa.industrial.understanding.terminology import Alternative

        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        t = book.propose(
            REV1,
            "الفيلر",
            "machine",
            entity_type="MACHINE",
            canonical_id="M2",
            alternatives=(
                Alternative(concept="filler", cues=("زجاجة",)),
                Alternative(concept="feeder", cues=("خام",)),
            ),
        )
        book.confirm(REV1, t.term_id)
        res = IdentityResolver(a.state(), a.fo.ontology, book.entries()).resolve("الفيلر")
        assert res.outcome is Outcome.AMBIGUOUS and res.entity_id is None

    def test_open_dispute_makes_the_identity_conflicted(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.dispute(a.svc, "M2", "is Press 200 really called that?")
        res = IdentityResolver(a.state(), a.fo.ontology).resolve("Press 200")
        assert res.outcome is Outcome.CONFLICTED

    def test_aliases_become_terminology_proposals_never_confirmed(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        made = propose_terminology_for_entities(book, SVC1, a.state())
        assert made and all(t.status.value == "PROPOSED" and t.canonical_id for t in made)
        assert not propose_terminology_for_entities(book, SVC1, a.state())  # idempotent
        with pytest.raises(AuthorizationError):
            propose_terminology_for_entities(
                TerminologyBook(InMemoryTerminologyStore(), "F2"), SVC1, a.state()
            )


# ============================================================================ temporal / history
class TestTemporal:
    def test_correction_preserves_the_earlier_statement(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        before_rev = a.fo.revision
        cap = a.state().entities["CAP1"]
        a.fo.update(
            a.hum,
            "CAP1",
            expected_version=cap.version,
            attrs={"amount": q("150", "h"), "basis": "AVAILABLE_TIME"},
            evidence=EV,
            note="re-measured",
        )
        now = a.state().entities["CAP1"]
        old = a.fo.view(a.hum, as_of_revision=before_rev).entities["CAP1"]
        assert now.data.attrs["amount"] == q("150", "h") and now.version == cap.version + 1
        assert old.data.attrs["amount"] == q("160", "h") and old.version == cap.version
        history = [e for e in a.fo.events(a.hum) if e.object_id == "CAP1"]
        assert [e.entity.attrs["amount"].value for e in history] == ["160", "150"]  # type: ignore[union-attr]
        assert history[1].note == "re-measured" and history[1].actor_id == "ana"
        assert a.fo.view(a.hum, as_of_revision=0).entities == {}

    def test_historical_measurements_are_not_current_state(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.ent(
            "Measurement",
            "MS1",
            "Capacity last year",
            attrs={
                "value": q("120", "h"),
                "temporal_class": "HISTORICAL",
                "observed_at": "2025-01-01T00:00:00+00:00",
            },
        )
        a.ent(
            "Measurement",
            "MS2",
            "Capacity now",
            attrs={"value": q("150", "h"), "temporal_class": "CURRENT"},
        )
        a.rel("MEASURES", "MS1", "M1")
        a.rel("MEASURES", "MS2", "M1")
        eng = a.engine()
        assert {"MS1"} <= set(eng.outdated("2026-06-01T00:00:00+00:00"))
        assert "MS2" not in eng.outdated("2026-06-01T00:00:00+00:00")
        ic = interpretation_context(
            a.state(), a.fo.ontology, ["MS1", "MS2"], at="2026-06-01T00:00:00+00:00"
        )
        assert [f.object_id for f in ic.historical] == ["MS1"] and [
            f.object_id for f in ic.current
        ] == ["MS2"]

    def test_retire_keeps_history_and_closes_validity(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        rid = next(
            r.relation_id
            for r in a.state().relations.values()
            if r.data.relation == "PRODUCES" and r.data.target == "P2"
        )
        rec = a.fo.retire(
            a.hum,
            rid,
            expected_version=1,
            valid_until="2026-03-01T00:00:00+00:00",
            note="no longer made",
        )
        assert rec.status is Status.RETIRED
        eng = a.engine()
        assert rid not in {e.relation_id for e in eng.product_context("P2").edges}
        past = eng.retrieve(
            ContextQuery(
                roots=("P2",), relations=("PRODUCES",), valid_at="2026-02-01T00:00:00+00:00"
            )
        )
        future = eng.retrieve(
            ContextQuery(
                roots=("P2",), relations=("PRODUCES",), valid_at="2026-05-01T00:00:00+00:00"
            )
        )
        assert rid in {e.relation_id for e in past.edges} and rid not in {
            e.relation_id for e in future.edges
        }
        with pytest.raises(OntologyError):
            a.fo.update(a.hum, rid, expected_version=rec.version, evidence=EV)

    def test_hypothetical_and_assumed_data_never_leaks_into_current_context(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.hum, "Product", "What-if", entity_id="PH", basis=Basis.HYPOTHETICAL)
        a.fo.create_entity(a.hum, "Product", "Assumed", entity_id="PA", basis=Basis.ASSUMPTION)
        eng = a.engine()
        assert "PH" not in {n.entity_id for n in eng.retrieve(ContextQuery(roots=("P1",))).nodes}
        ctx = planning_context(a.state(), a.fo.ontology)
        assert all("PH" not in c.objects for c in ctx.candidates)
        assert a.state().entities["PH"].status is Status.HYPOTHETICAL


# ============================================================================ tamper / persistence
class TestIntegrityAndPersistence:
    def test_tampered_history_is_detected(self, two) -> None:  # type: ignore[no-untyped-def]
        store, _a, _ = two
        events = store.events("F1")
        i = next(k for k, e in enumerate(events) if e.object_id == "CAP1")
        forged = events[i].model_copy(update={"actor_id": "mallory"})
        store.tamper("F1", i + 1, forged)
        rep = verify_factory("F1", store.events("F1"), core_ontology())
        assert not rep.ok and any("hash" in m for m in rep.chain_errors)
        # even a re-hashed forgery breaks the link to the next event
        store.tamper("F1", i + 1, forged.model_copy(update={"sha": forged.body_sha256()}))
        assert any("broken chain" in m for m in verify_events("F1", store.events("F1")))

    def test_reordered_or_dropped_events_are_detected(self, two) -> None:  # type: ignore[no-untyped-def]
        store, _, _ = two
        ev = store.events("F1")
        assert verify_events("F1", ev) == []
        assert verify_events("F1", [ev[1], ev[0], *ev[2:]])
        assert verify_events("F1", ev[:3] + ev[4:])

    def test_persistence_across_restart_reconstructs_the_same_revision(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "onto.db"
        s1 = SqliteOntologyStore(db)
        a = build_factory(s1, "F1", HUM1, SVC1, nick="bet2af", mach_id="M1")
        cap = a.state().entities["CAP1"]
        a.fo.update(
            a.hum,
            "CAP1",
            expected_version=cap.version,
            attrs={"amount": q("150", "h"), "basis": "AVAILABLE_TIME"},
            evidence=EV,
        )
        rev, head, graph = a.fo.revision, a.fo.head_sha, a.state().sha256()
        before = a.engine().line_context("L1").result_sha256

        s2 = SqliteOntologyStore(db)  # "restart": brand-new objects over the same file
        fo2 = FactoryOntology(s2, "F1", clock=make_clock())
        st = fo2.view(HUM1)
        assert (st.revision, st.head_sha, st.sha256()) == (rev, head, graph)
        assert verify_factory("F1", s2.events("F1"), core_ontology()).ok
        assert ContextEngine(st, fo2.ontology).line_context("L1").result_sha256 == before
        fo2.create_entity(
            HUM1,
            "Product",
            "After restart",
            entity_id="P30",
            basis=Basis.HUMAN_CONFIRMED,
            evidence=EV,
        )
        assert fo2.revision == rev + 1 and verify_factory("F1", s2.events("F1"), core_ontology()).ok

    def test_sqlite_enforces_optimistic_concurrency_and_isolation(self, tmp_path: Path) -> None:
        s = SqliteOntologyStore(tmp_path / "o.db")
        a = build_factory(s, "F1", HUM1, SVC1, nick="n", mach_id="M1")
        assert s.revision("F2") == 0 and s.events("F2") == []
        with pytest.raises(StoreError) as e:
            s.append("F1", [], expected_revision=a.fo.revision - 5)
        assert e.value.code == "REVISION_CONFLICT"
        ev = s.events("F1")[-1]
        with pytest.raises(StoreError) as e2:  # replaying an event into a taken slot
            s.append("F1", [ev], expected_revision=s.revision("F1") - 1)
        assert e2.value.code in {"REVISION_CONFLICT", "BAD_SEQUENCE"}

    def test_sqlite_tampering_is_caught_by_verification(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        s = SqliteOntologyStore(db)
        build_factory(s, "F1", HUM1, SVC1, nick="n", mach_id="M1")
        con = sqlite3.connect(db)
        con.execute("UPDATE ontology_events SET body = replace(body, '\"160\"', '\"999\"')")
        con.commit()
        con.close()
        rep = verify_factory("F1", SqliteOntologyStore(db).events("F1"), core_ontology())
        assert not rep.ok and rep.chain_errors

    def test_sqlite_row_moved_to_another_key_is_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "m.db"
        s = SqliteOntologyStore(db)
        build_factory(s, "F1", HUM1, SVC1, nick="n", mach_id="M1")
        con = sqlite3.connect(db)
        con.execute("UPDATE ontology_events SET factory_id='F2' WHERE seq=1")
        con.commit()
        con.close()
        with pytest.raises(StoreError) as e:
            SqliteOntologyStore(db).events("F2")
        assert e.value.code == "ROW_TAMPERED"


# ============================================================================ retrieval
class TestRetrieval:
    def test_line_and_machine_context(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        line = eng.line_context("L1")
        ids = {n.entity_id for n in line.nodes}
        assert (
            {"L1", "M1", "M2", "P1", "P2", "CAP1"} <= ids and "M3" not in ids and not line.truncated
        )
        assert all(e.evidence for e in line.edges) and all(n.evidence for n in line.nodes)
        mc = eng.machine_context("M1")
        assert {"L1", "CAP1", "P1", "P2"} <= {n.entity_id for n in mc.nodes}

    def test_product_resources_materials_and_demand(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        assert {"M1", "CAP1", "MAT1", "D1"} <= {
            n.entity_id for n in eng.product_context("P1").nodes
        }
        aff = eng.resources_affecting("P1")
        assert {"M1", "CAP1"} <= {n.entity_id for n in aff.nodes} and "MAT1" not in {
            n.entity_id for n in aff.nodes
        }

    def test_upstream_and_downstream_dependencies(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        down = eng.dependencies("OP3", downstream=False)  # OP3 PRECEDES OP4...: out-edges
        assert [n.entity_id for n in down.nodes if n.entity_id != "OP3"] == ["OP4", "OP5", "OP6"]
        up = eng.dependencies("OP3", downstream=True)
        assert [n.entity_id for n in up.nodes if n.entity_id != "OP3"] == ["OP2", "OP1"]

    def test_scope_filters(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        r = eng.retrieve(
            ContextQuery(
                roots=("siteA",),
                direction=Direction.OUT,
                relations=("CONTAINS",),
                max_depth=3,
                line_id="L1",
            )
        )
        got = {n.entity_id for n in r.nodes}
        assert {"L1", "M1", "M2"} <= got and not ({"L2", "M3"} & got)
        assert r.excluded.get("out_of_scope", 0) >= 1 and any(
            "outside the requested scope" in x for x in r.limitations
        )

    def test_depth_node_and_edge_budgets_with_explicit_truncation(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        shallow = eng.retrieve(
            ContextQuery(
                roots=("OP1",), direction=Direction.OUT, relations=("PRECEDES",), max_depth=2
            )
        )
        assert [n.entity_id for n in shallow.nodes] == [
            "OP1",
            "OP2",
            "OP3",
        ] and not shallow.truncated
        small = eng.retrieve(
            ContextQuery(
                roots=("OP1",),
                direction=Direction.OUT,
                relations=("PRECEDES",),
                max_depth=6,
                max_nodes=3,
            )
        )
        assert len(small.nodes) == 3 and small.truncated and "MAX_NODES" in small.truncation_reasons
        assert any("truncated" in x for x in small.limitations)
        edges = eng.retrieve(
            ContextQuery(
                roots=("OP1",),
                direction=Direction.OUT,
                relations=("PRECEDES",),
                max_depth=6,
                max_edges=2,
            )
        )
        assert len(edges.edges) == 2 and edges.truncated and "MAX_EDGES" in edges.truncation_reasons

    def test_cycles_in_unconfirmed_data_cannot_loop_the_traversal(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_relation(a.svc, "PRECEDES", "OP6", "OP1")  # a proposed cycle
        eng = a.engine()
        r = eng.retrieve(
            ContextQuery(
                roots=("OP1",),
                direction=Direction.OUT,
                relations=("PRECEDES",),
                max_depth=6,
                include_proposed=True,
            )
        )
        ids = [n.entity_id for n in r.nodes]
        assert sorted(ids) == sorted(set(ids)) and len(ids) == 6
        assert any("non-authoritative" in x for x in r.limitations)  # proposals are flagged
        plain = eng.retrieve(
            ContextQuery(
                roots=("OP1",), direction=Direction.OUT, relations=("PRECEDES",), max_depth=6
            )
        )
        assert plain.excluded.get("proposed") == 1 and any(
            "proposals were excluded" in x for x in plain.limitations
        )

    def test_malformed_and_oversized_queries(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        eng = a.engine()
        bad: list[object] = [
            {"roots": []},
            {"roots": ["M1"] * 51},
            {"roots": ["M1"], "max_depth": 99},
            {"roots": ["M1"], "max_nodes": 10_000},
            {"roots": ["M1"], "max_edges": -1},
            {"roots": ["M1"], "valid_at": "yesterday-ish"},
            {"roots": ["M1"], "surprise": True},
            {"roots": "M1"},
            None,
            "roots",
        ]
        for raw in bad:
            with pytest.raises(RetrievalError) as e:
                ContextQuery.parse(raw)
            assert e.value.code == "INVALID_QUERY"
        with pytest.raises(RetrievalError) as e2:
            eng.retrieve(ContextQuery(roots=("ghost",)))
        assert e2.value.code == "UNKNOWN_ROOT"
        with pytest.raises(OntologyError):
            eng.retrieve(ContextQuery(roots=("M1",), relations=("TELEPORTS",)))
        with pytest.raises(OntologyError):
            eng.retrieve(ContextQuery(roots=("M1",), concepts=("Dragon",)))

    def test_deterministic_regardless_of_creation_order(self) -> None:
        def build(order: list[str]) -> ContextEngine:
            fo = FactoryOntology(InMemoryOntologyStore(), "F1", clock=make_clock())
            f = Fx(fo, HUM1, SVC1)
            f.ent("Site", "s1", "S")
            f.ent("ProductionLine", "L1", "L", scope=Scope(site_id="s1"))
            for m in order:
                f.ent("Machine", m, m, scope=Scope(site_id="s1"))
            for m in order:
                f.rel("CONTAINS", "L1", m)
            return ContextEngine(fo.view(HUM1), fo.ontology)

        r1 = build(["M1", "M2", "M3"]).retrieve(
            ContextQuery(roots=("L1",), direction=Direction.OUT)
        )
        r2 = build(["M3", "M1", "M2"]).retrieve(
            ContextQuery(roots=("L1",), direction=Direction.OUT)
        )
        assert (
            [n.entity_id for n in r1.nodes]
            == [n.entity_id for n in r2.nodes]
            == ["L1", "M1", "M2", "M3"]
        )
        assert [(e.source, e.target) for e in sorted(r1.edges, key=lambda e: e.target)] == [
            (e.source, e.target) for e in sorted(r2.edges, key=lambda e: e.target)
        ]

    def test_repeat_queries_are_identical(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        e1, e2 = a.engine(), a.engine()
        assert e1.line_context("L1").result_sha256 == e2.line_context("L1").result_sha256
        assert e1.line_context("L1").model_dump() == e2.line_context("L1").model_dump()

    def test_unresolved_and_gaps_expose_the_unknown(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "Idea", entity_id="P5")
        u = a.engine().unresolved()
        assert "P5" in u.proposed_entities
        gaps = {(g.code, g.entity_id) for g in u.gaps}
        assert ("NO_CAPACITY", "M2") in gaps and ("NO_CAPACITY", "M1") not in gaps
        assert ("NO_PRODUCER", "P1") not in gaps and ("NO_REQUIREMENT", "P2") in gaps

    def test_evidence_for_a_relationship(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        rid = next(
            r.relation_id for r in a.state().relations.values() if r.data.relation == "HAS_CAPACITY"
        )
        assert a.engine().evidence_for(rid) == EV
        with pytest.raises(RetrievalError):
            a.engine().evidence_for("rel-99999")

    def test_proposals_listed_only_when_asked_and_flagged(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.fo.create_entity(a.svc, "Product", "Idea", entity_id="P5")
        a.fo.create_relation(a.svc, "PRODUCES", "M2", "P5")
        eng = a.engine()
        r = eng.retrieve(
            ContextQuery(
                roots=("M2",),
                relations=("PRODUCES",),
                include_proposed=True,
                direction=Direction.OUT,
            )
        )
        assert {n.entity_id: n.status for n in r.nodes}["P5"] is Status.PROPOSED
        assert any("non-authoritative" in x for x in r.limitations)
        assert [
            n.entity_id
            for n in eng.retrieve(ContextQuery(roots=("M2",), relations=("PRODUCES",))).nodes
        ] == ["M2"]


# ============================================================================ integrations
class TestUnderstandingIntegration:
    def test_report_mentions_link_to_canonical_entities_without_new_authority(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F1")
        t = book.propose(
            REV1, "stacker", "machine", entity_type="MACHINE", canonical_id="M1", language="en"
        )
        book.confirm(REV1, t.term_id)
        t2 = book.propose(
            REV1, "the mystery press", "machine", entity_type="MACHINE", canonical_id="M404"
        )
        book.confirm(REV1, t2.term_id)
        report = UnderstandingSession(book).submit(
            SourceDocument(
                source_id="s1",
                factory_id="F1",
                text="The stacker produces 40 pieces per hour. The mystery press is slow.",
            )
        )
        before = report.report_sha256
        links = link_report(report, IdentityResolver(a.state(), a.fo.ontology, book.entries()))
        assert links.factory_id == "F1" and links.revision == a.fo.revision
        by_text = {m.text.lower(): m.resolution for m in links.mentions}
        assert (
            by_text["stacker"].outcome is Outcome.RESOLVED and by_text["stacker"].entity_id == "M1"
        )
        mystery = next(v for k, v in by_text.items() if "mystery" in k)
        assert mystery.outcome is Outcome.UNRESOLVED and mystery.entity_id is None
        assert links.unresolved_mentions  # preserved, not guessed
        assert report.report_sha256 == before  # the report is untouched
        assert any(c.subject and c.subject.resolution.entity_id == "M1" for c in links.claims)
        # the claim stays an untrusted understanding artefact
        assert "not human-approved" in report.disclaimer

    def test_cross_factory_report_is_rejected(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _b = two
        book = TerminologyBook(InMemoryTerminologyStore(), "F2")
        rep = UnderstandingSession(book).submit(
            SourceDocument(source_id="s", factory_id="F2", text="Machine A is down.")
        )
        with pytest.raises(ValueError, match="FACTORY_MISMATCH"):
            link_report(rep, IdentityResolver(a.state(), a.fo.ontology))

    def test_bounded_reproducible_context_snapshot(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        s1 = understanding_snapshot(a.state(), a.fo.ontology, ["L1"], max_nodes=8)
        s2 = understanding_snapshot(a.state(), a.fo.ontology, ["L1"], max_nodes=8)
        assert s1 == s2 and len(s1.entities) <= 8 and s1.truncated and s1.snapshot_sha256
        assert any(
            e[0] == "M1" and "stacker" in e[3]
            for e in understanding_snapshot(a.state(), a.fo.ontology, ["L1"]).entities
        )
        assert all(rid.startswith("rel-") for rid, *_ in s1.relations)


class TestPlanningIntegration:
    def test_confirmed_structure_maps_to_ledger_keys_with_lineage(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        ctx = planning_context(a.state(), a.fo.ontology, line_id="L1")
        got = {c.key: c.value for c in ctx.candidates}
        assert got["resource:M1:capacity"] == "160" and got["resource:M1:unit"] == "h"
        assert got["consumption:M1:P1"] == "2" and got["consumption:M1:P1:unit"] == "h/piece"
        assert got["product:P1:min_quantity"] == "50" and got["product:P1:bound_unit"] == "piece"
        assert (
            got["product:P1:value_per_unit"] == "50" and got["product:P1:value_unit"] == "EGP/piece"
        )
        assert got["problem:currency"] == "EGP"
        assert ("M1", "P1") in ctx.process_compatibility and (
            "L1",
            "CONTAINS",
            "M1",
        ) in ctx.line_structure
        assert ctx.material_consumption == (("P1", "MAT1", "3", "kg/piece"),)
        lin = next(c for c in ctx.candidates if c.key == "resource:M1:capacity")
        assert {"M1", "CAP1"} <= set(lin.objects) and lin.evidence and lin.revision == a.fo.revision
        assert "no solver result" in ctx.limitations[0]

    def test_unsupported_and_conflicting_information_is_rejected_not_mapped(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.ent(
            "Capacity",
            "CAP2",
            "Throughput",
            attrs={"amount": q("40", "piece/h"), "basis": "THROUGHPUT"},
        )
        a.rel("HAS_CAPACITY", "M2", "CAP2")
        a.fo.create_relation(a.svc, "REQUIRES", "P2", "M2", attrs={"amount": q("1", "h/piece")})
        a.ent("Capacity", "CAP3", "Bad", attrs={"amount": q("10", "kg"), "basis": "AVAILABLE_TIME"})
        a.rel("HAS_CAPACITY", "M3", "CAP3")
        ctx = planning_context(a.state(), a.fo.ontology)
        rej = {r.key: r.reason for r in ctx.rejected}
        assert rej["resource:M2:capacity"].startswith("CAPACITY_BASIS_NOT_MAPPED")
        assert rej["consumption:M2:P2"].startswith("NOT_AUTHORITATIVE")
        assert rej["resource:M3:capacity"].startswith(
            "UNIT_NOT_TIME"
        )  # 10 kg is not available time
        assert "resource:M3:capacity" not in {c.key for c in ctx.candidates}
        assert "resource:M2:capacity" not in {c.key for c in ctx.candidates}

    def test_disputed_capacity_is_not_planned_on(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        rid = next(
            r.relation_id for r in a.state().relations.values() if r.data.relation == "HAS_CAPACITY"
        )
        a.fo.dispute(a.svc, rid, "conflicting shift report")
        ctx = planning_context(a.state(), a.fo.ontology)
        assert "resource:M1:capacity" not in {c.key for c in ctx.candidates}
        assert any(r.key == "resource:M1:capacity" and r.reason == "DISPUTED" for r in ctx.rejected)

    def test_time_awareness_expired_capacity_is_excluded(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        a.ent(
            "Capacity",
            "CAP5",
            "Short lived",
            attrs={"amount": q("10", "h"), "basis": "AVAILABLE_TIME"},
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until="2026-02-01T00:00:00+00:00",
        )
        a.rel("HAS_CAPACITY", "M3", "CAP5")
        late = planning_context(a.state(), a.fo.ontology, at="2026-05-01T00:00:00+00:00")
        assert any(
            r.key == "resource:M3:capacity" and r.reason == "NOT_VALID_AT_TIME"
            for r in late.rejected
        )
        early = planning_context(a.state(), a.fo.ontology, at="2026-01-15T00:00:00+00:00")
        assert "resource:M3:capacity" in {c.key for c in early.candidates}

    def test_candidates_enter_the_ledger_only_as_untrusted_proposals(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        ctx = planning_context(a.state(), a.fo.ontology, line_id="L1")
        cands = to_understanding_candidates(ctx, source_label="ontology")
        with pytest.raises(AuthorizationError):
            emit_proposals(cands, Ledger(), HUM1, "x")
        led, rejected = emit_proposals(cands, Ledger(), SVC1, f"ontology:F1@{ctx.revision}")
        assert led.entries and not rejected
        assert all(e.basis is not None and e.basis.value == "PROPOSAL" for e in led.entries)
        assert all(c.support[0].startswith("onto:F1@") for c in cands)

    def test_unknown_line_is_rejected(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        with pytest.raises(ValueError, match="UNKNOWN_LINE"):
            planning_context(a.state(), a.fo.ontology, line_id="L99")


class TestDecisionContext:
    def test_time_and_provenance_aware_interpretation(self, two) -> None:  # type: ignore[no-untyped-def]
        _, a, _ = two
        rid = next(
            r.relation_id for r in a.state().relations.values() if r.data.relation == "HAS_CAPACITY"
        )
        a.fo.dispute(a.svc, rid, "unclear")
        ic = interpretation_context(
            a.state(), a.fo.ontology, ["P1", rid], at="2026-06-01T00:00:00+00:00"
        )
        assert [f.object_id for f in ic.current] == ["P1"] and [
            f.object_id for f in ic.disputed
        ] == [rid]
        assert ic.current[0].evidence == EV and ic.open_disputes
        with pytest.raises(ValueError, match="UNKNOWN_OBJECT"):
            interpretation_context(
                a.state(), a.fo.ontology, ["nope"], at="2026-06-01T00:00:00+00:00"
            )


# ============================================================================ the whole journey
def test_full_synthetic_factory_journey(tmp_path: Path) -> None:
    """The 15-step definition of done, end to end, on synthetic data only."""
    db = tmp_path / "journey.db"
    store = SqliteOntologyStore(db)
    # 1-6: two isolated factories, versioned schema, identities, entities, relations, evidence, units
    a = build_factory(store, "F1", HUM1, SVC1, nick="bet2af", mach_id="M1")
    b = build_factory(store, "F2", HUM2, SVC2, nick="bet2af", mach_id="K9")
    assert a.fo.ontology.version == b.fo.ontology.version and len(a.fo.ontology.sha256()) == 64
    # 7: factory-specific nickname -> correct machine
    assert IdentityResolver(a.state(), a.fo.ontology).resolve("bet2af").entity_id == "M1"
    assert IdentityResolver(b.state(), b.fo.ontology).resolve("bet2af").entity_id == "K9"
    # 8: ambiguity is reported, not resolved by guessing
    amb = IdentityResolver(a.state(), a.fo.ontology).resolve("big one")
    assert amb.outcome is Outcome.AMBIGUOUS and len(amb.candidates) == 2
    # 9: a correction keeps the historical capacity observation
    rev0 = a.fo.revision
    a.fo.update(
        HUM1,
        "CAP1",
        expected_version=1,
        attrs={"amount": q("150", "h"), "basis": "AVAILABLE_TIME"},
        evidence=EV,
        note="re-measured",
    )
    assert a.fo.view(HUM1, as_of_revision=rev0).entities["CAP1"].data.attrs["amount"] == q(
        "160", "h"
    )
    # 10-11: current production-line context and a bounded snapshot for Understanding
    line = a.engine().line_context("L1")
    assert "M1" in {n.entity_id for n in line.nodes} and not line.truncated
    snap = understanding_snapshot(a.state(), a.fo.ontology, ["L1"], max_nodes=10)
    assert len(snap.entities) <= 10 and snap.snapshot_sha256
    # 12: product/resource relationships -> planning candidates
    ctx = planning_context(a.state(), a.fo.ontology, line_id="L1")
    got = {c.key: c.value for c in ctx.candidates}
    assert got["resource:M1:capacity"] == "150" and got["consumption:M1:P1"] == "2"
    # 13: unsupported / conflicting information is rejected
    with pytest.raises(OntologyError):
        a.rel("PRODUCES", "P1", "M1")
    with pytest.raises(OntologyError):
        a.rel("PRECEDES", "OP6", "OP1")
    a.fo.create_relation(SVC1, "REQUIRES", "P2", "M2", attrs={"amount": q("1", "h/piece")})
    assert "consumption:M2:P2" in {
        r.key for r in planning_context(a.state(), a.fo.ontology).rejected
    }
    # 14: restart -> same verified graph revision
    rev, head, g1 = a.fo.revision, a.fo.head_sha, a.state().sha256()
    fo2 = FactoryOntology(SqliteOntologyStore(db), "F1", clock=make_clock())
    st2 = fo2.view(HUM1)
    assert (st2.revision, st2.head_sha, st2.sha256()) == (rev, head, g1)
    assert verify_factory("F1", SqliteOntologyStore(db).events("F1"), core_ontology()).ok
    assert verify_factory("F2", SqliteOntologyStore(db).events("F2"), core_ontology()).ok
    # 15: Factory A can neither read nor modify Factory B
    with pytest.raises(AuthorizationError):
        b.fo.view(HUM1)
    with pytest.raises(AuthorizationError):
        b.fo.create_entity(HUM1, "Product", "Sneak", basis=Basis.HUMAN_CONFIRMED, evidence=EV)
    assert "K9" not in a.state().entities
