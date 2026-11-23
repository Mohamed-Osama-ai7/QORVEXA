# Industrial Ontology & Context Engine (Factory Knowledge Fabric)

Package `backend/src/qorvexa/industrial/ontology/` · tests `backend/tests/test_industrial_ontology.py` (70).
It stores and retrieves knowledge about one factory as a connected, typed, evidence-backed graph.
It never solves, never validates optimisation mathematics and never turns a proposal into a fact.
All fixtures are **synthetic**; nothing here describes a real plant.

## Modules
| Module | Responsibility |
|---|---|
| `schema.py` | versioned `Ontology` (24 concepts, 14 relations), `ConceptSchema` / `RelationSchema` / `AttrSpec`, is-a hierarchy, endpoint pairs, cardinality, acyclic flags, scope requirements, deprecation + migration policy, `ExtensionPack`, attribute validation (units via the Units Engine only) |
| `model.py` | `EvidenceRef`, `Basis`, `Status`, typed `Event` (hash-chained log line), `EntityRecord`, `RelationRecord`, `DisputeRecord` |
| `graph.py` | `GraphState`: pure fold of the event log at a revision; adjacency, validity windows, reachability, canonical graph hash, structural `integrity_issues` |
| `service.py` | `FactoryOntology`: the only write path; authorization, validation, optimistic concurrency, idempotency, one event per change |
| `store.py` | `OntologyStore` protocol, `InMemoryOntologyStore`, `SqliteOntologyStore` (own table `ontology_events`, WAL, atomic append, PK `(factory, seq)`) |
| `integrity.py` | `verify_events` (hashes, sequence, factory binding, chain) and `verify_factory` (+ replayed graph invariants) |
| `resolution.py` | `IdentityResolver` (RESOLVED / AMBIGUOUS / UNRESOLVED / CONFLICTED with evidence), terminology bridge, `propose_terminology_for_entities` |
| `retrieval.py` | `ContextEngine`, `ContextQuery`, budgets, truncation flags, canned queries, gaps / unresolved / outdated; `ContextRetriever` and `CandidateRanker` extension points |
| `adapters.py` | Understanding links + bounded snapshot, Formulation/Planning candidates with lineage, Decision Intelligence interpretation context |

## Ontology
Concepts: Organization, Facility, Site, ProductionLine, WorkCenter, Equipment ← Machine, Tool, Product, Material ← Component, Resource, Capacity,
Operation, Process, ProcessStage, Shift, ProductionPeriod, ProductionOrder, Demand, Constraint, Dependency, Measurement, Observation.
Relations (each with explicit allowed `(source, target)` concept pairs): `CONTAINS` (acyclic, one container per child), `LOCATED_AT` (one at a time),
`OPERATES`, `PRODUCES`, `CONSUMES`, `REQUIRES`, `DEPENDS_ON` (acyclic), `ASSIGNED_TO`, `SUPPLIES`, `PRECEDES` (acyclic), `HAS_CAPACITY`,
`HAS_CONSTRAINT`, `HAS_DEMAND`, `MEASURES`.
`BELONGS_TO` is deliberately not a relation: it is the inverse of `CONTAINS`, available as a traversal direction (`Direction.IN`), so no redundant or
contradictory pair can exist. Machine/line/site scope is required where meaningful (`scope_required`) and must reference real entities of the same factory.
Domain packs are added with `Ontology.extend(ExtensionPack)`; the core object is immutable; re-declaring an id needs a strictly higher version and a
migration note (`SCHEMA_OVERRIDE`); deprecated concepts/relations reject new use (`CONCEPT_DEPRECATED`) while old records keep their `schema_version`.

## Truth model
`Basis`: `HUMAN_CONFIRMED`, `IMPORTED_VERIFIED` (a human attests), `PROPOSAL`, `ASSUMPTION`, `HYPOTHETICAL`. Only `Status.CONFIRMED` is authoritative.
* LLM/extractor principals (`Role.EXTRACTOR`, non-human) can create **PROPOSED** records and raise **disputes** only.
* Every other status, `confirm`, `reject`, `retire`, `resolve_dispute` and every update of a non-proposal needs a **HUMAN** principal with PLANNER or
  APPROVER whose `Principal.factories` contains the factory (`HUMAN_APPROVAL_REQUIRED`, `FACTORY_FORBIDDEN`). Principals are never built from requests.
* A CONFIRMED record needs ≥1 evidence ref; a CONFIRMED relation needs CONFIRMED endpoints; confirmation re-runs endpoint, duplicate, cardinality and
  cycle checks. A retire is refused while live relations remain.
* Disputes keep conflicting evidence visible; disputed records leave default retrieval, planning and identity resolution until a human resolves them.
* Historical vs current: `Measurement.temporal_class`; HISTORICAL measurements appear in `outdated()` / `InterpretationContext.historical`, never as current state.

## Time and history
Bitemporal: **valid time** (`valid_from` / `valid_until`) lives on the record; **recorded time** is the factory revision (`seq`) plus `at`.
`FactoryOntology.view(principal, as_of_revision=n)` rebuilds the model exactly as known at revision *n*; `ContextQuery.valid_at` filters by validity.
An `UPDATE` appends version *n+1*; version *n* stays in the log and in earlier revisions. Non-overlapping windows may coexist where cardinality would
otherwise forbid it. No event is ever rewritten.

## Persistence and integrity
Per-factory append-only log, `prev_sha → sha` chain, optimistic append (`REVISION_CONFLICT`), object versions (`VERSION_CONFLICT`), idempotency keys
(`IDEMPOTENCY_CONFLICT`), deterministic canonical JSON hashes. `verify_factory` checks hashes, sequence, factory binding and replays graph invariants
(orphans, incompatible endpoints, missing evidence, authority on non-authority, duplicates, cycles, cardinality). SQLite rows are bound to their key
(`ROW_TAMPERED`). The store uses only its own table; governance tables and API persistence are untouched.

## Identity resolution
Sources: entity id, official `asset_id`, name, confirmed aliases, and confirmed Understanding terminology (`resolve_surface`, reused, not duplicated).
Evidence per candidate: `official_id`, `line_match`, `site_match`, `department_match`, `relation_support:<id>`, `term:<id>`. Exclusions stay visible
(`OTHER_LINE`, `CONCEPT_MISMATCH`, `NOT_CONFIRMED`, `TERM_TARGET_MISSING`). Outcomes: one supported candidate → RESOLVED; several with a strict evidence
winner → RESOLVED with alternatives kept; otherwise AMBIGUOUS; none (or only proposals) → UNRESOLVED; terminology vs ontology disagreement or an open
dispute → CONFLICTED. A declared terminology ambiguity is never guessed.

## Retrieval contract
`ContextQuery(roots, relations, direction, max_depth≤6, max_nodes≤200, max_edges≤500, concepts, site_id/line_id/department, valid_at, include_*)`;
`ContextQuery.parse(raw)` raises `RetrievalError(INVALID_QUERY)`. Deterministic BFS, sorted by `(depth, id)`, visited-set cycle protection, a 5 000-expansion
budget, explicit `truncated` + `truncation_reasons`, counted `excluded`, human-readable `limitations`, evidence on every node/edge, `result_sha256`.
Canned queries: `machine_context`, `line_context`, `product_context`, `resources_affecting`, `dependencies(downstream=)`, `facts_for_problem`,
`evidence_for`, `unresolved()`, `knowledge_gaps()`, `outdated(at)`. No embeddings; `CandidateRanker` is only a future hook.

## Integration adapters
* **Understanding:** `link_report(report, resolver)` → `UnderstandingLinks` (per mention and per claim subject/related; unresolved preserved; report untouched; wrong
  factory rejected). `understanding_snapshot(...)` → bounded, hashed `ContextSnapshot`. `propose_terminology_for_entities` offers aliases as PROPOSED terms.
* **Formulation / Production Planning:** `planning_context(state, ontology, line_id=, at=)` → ledger-keyed `PlanningCandidate`s (`resource:R:capacity|unit`,
  `consumption:R:P|:unit`, `product:P:min_quantity|max_quantity|bound_unit|value_per_unit|value_unit`, `problem:currency`) with entity/relation lineage,
  evidence and revision, plus line structure, product/resource, capacity, material and producer/product compatibility. Anything unmappable is in
  `rejected` with a reason (`NOT_AUTHORITATIVE`, `DISPUTED`, `NOT_VALID_AT_TIME`, `CAPACITY_BASIS_NOT_MAPPED`, `UNIT_NOT_TIME`, `UNIT_NOT_A_RATE`,
  `MISSING_AMOUNT`, `CONFLICTING_VALUES`). `to_understanding_candidates` → `emit_proposals` (EXTRACTOR principal) → **untrusted** ledger proposals.
* **Units:** `parse_unit`, `Quantity.to` only; `convert_capacity` is a thin call into the Units Engine (currency↔time is refused there).
* **Decision Intelligence:** `interpretation_context(...)` separates current, historical/stale and disputed facts with provenance and open disputes; context only.

## Antigravity integration requirements
1. Build `Principal(..., factories=frozenset({factory_id}))` only from the authenticated token; humans: PLANNER/APPROVER; services: EXTRACTOR, non-human.
2. One `FactoryOntology(store, factory_id)` per factory; one shared `SqliteOntologyStore(path)` (own table) is fine. Never accept a factory id from a body without
   checking the principal; `view()`/writes already raise `FACTORY_FORBIDDEN`.
3. Map error codes (`AuthorizationError`, `OntologyError.code`, `StoreError.code`, `RetrievalError.code`) to 403/404/409/422; `VERSION_CONFLICT` and
   `REVISION_CONFLICT` are 409; send `expected_version` / `expected_revision` from the client's last read; pass `idempotency_key` on retries.
4. Show status, basis, evidence, validity and dispute state on every fact; show `limitations` and `truncated`; never label a PROPOSED record as confirmed.
5. Wire `link_report` after each Understanding submit and keep UNRESOLVED/AMBIGUOUS/CONFLICTED visible; run `verify_factory` on startup or on demand.
6. Route planning candidates to the ledger only through `emit_proposals`; humans confirm in the existing governed-session endpoints.

## Limitations (genuine)
* Graph is folded in memory per factory (revisions are replayed from the log); fine for thousands of objects, not tuned for millions. No snapshots/compaction yet.
* Identity matching is exact on normalized surfaces (reuse of the terminology normalizer): no fuzzy/phonetic matching, so unseen spellings stay UNRESOLVED.
* Attribute updates replace the whole attribute set; there is no field-level merge, no bulk import API and no schema-migration executor (policy is enforced,
  data migration is manual: retire + recreate).
* Cardinality/cycle rules are enforced for CONFIRMED records only; proposals may conflict and are reported, not blocked.
* Time handling is ISO-8601 instants; no calendars, shift patterns or time zones beyond the offsets given. Valid-time corrections are modelled as versions, not
  as retroactive re-statements of an earlier valid interval.
* Only available-time capacity with a plain time unit, rate-unit consumption, demand bounds and margins map to the LP slice; setup, sequencing and other
  features are not mapped.
* The synthetic factory exercises the logic; it is not evidence about real plants.
