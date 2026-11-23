# Autonomous Problem Formulation (Claude-owned, backend)

Package: `backend/src/qorvexa/industrial/formulation/` · Tests: `backend/tests/test_formulation.py` (24).

## Contract
- Facts are claims in an append-only, hash-chained `Ledger`. Keys: `problem:<field>`, `product:<id>:<field>`,
  `resource:<id>:<field>`, `consumption:<resource>:<product>`, `feature:<name>`.
- Human-only operations: `assert_fact`, `confirm`, `assume`, `retract`, `resolve`. LLM/system actors may only `propose`.
- Assumptions are allow-listed (min_quantity=0, max_quantity=NONE, consumption=0) and need a rationale.
- `formulate(ledger)` -> `FormulationResult(state, problem, compiled, record)`. Status: READY | NEEDS_CLARIFICATION |
  CONTRADICTED | UNSUPPORTED | INVALID_MODEL. Only READY carries a problem. `state.issues` hold deterministic questions.
- `record` traces every model value to a claim; compiled lineage contains `formulation_ledger:<head>`.
- `propose_from_draft(ledger, draft, actor)` ingests untrusted extractions as proposals (atomic).

## Limitations
Actor identity must come from the authenticated API layer. Units are string-checked, not converted. Continuous LP
production planning only. No NL extractor yet. Advisory only.

---
# Antigravity backlog (Antigravity owns frontend/integration; Claude owns `formulation/`, solver, decision code — do not edit them)

## AG-01 Formulation API and session persistence
**Objective:** expose the formulation engine over authenticated HTTP with durable ledgers.
**Why:** the engine has no transport; identity and persistence are explicit trust boundaries.
**Baseline:** pure library, in-memory immutable `Ledger`.
**Behavior:** create session; append operations (propose/assert/confirm/assume/retract/resolve); GET state (issues,
facts); POST `/build` only when READY returning problem hash, record, compiled spec hash.
**Requirements:** actor identity derived from the authenticated principal only (never request body); HUMAN kind only for
authenticated users, LLM kind only for the internal extractor service; store entries as JSON rows, reload and call
`Ledger.verify()` on every load; optimistic concurrency on ledger head; map `FormulationError.code` to 4xx.
**Inspect:** `industrial/formulation/*`, existing FastAPI app/router patterns, auth middleware.
**Create:** `backend/src/qorvexa/api/formulation_routes.py`, storage module, `tests/test_formulation_api.py`.
**Depends on:** none. **Acceptance:** all ops round-trip; tampered stored row -> 409/500 with `LEDGER_TAMPERED`; LLM
actor cannot confirm; stale head rejected. **Tests:** unit, API integration, one E2E to a real HiGHS solve.
**Edge cases:** concurrent appends, oversized payloads, float JSON values, duplicate keys.
**Security:** no client-supplied actor, size limits, audit log of every op. **Out of scope:** UI, changing engine code.

## AG-02 Formulation Studio (guided clarification UI)
**Objective:** React workspace where a planner resolves gaps, confirms proposals, approves assumptions, resolves
contradictions, and sees a live READY/blocked state.
**Why:** humans are the authority; the UI must make unknowns, proposals and assumptions visibly different.
**Baseline:** existing React app, no formulation screens. **Depends on:** AG-01 contract.
**Requirements:** render `issues` as question cards (candidates shown, never preselected); proposals visually
"unconfirmed"; "accept defaults" button requires rationale text and lists every key affected; blocked build button with
reasons; provenance drawer per fact (claim seq, actor, basis, rationale); ledger-head conflict handling.
**Tests:** component tests, API-mocked flows, Playwright E2E: draft -> clarify -> READY -> build.
**Edge cases:** contradiction resolution, retracting features, stale head, 1000-entry ledgers.
**Security:** never display or send actor identity from the client. **Out of scope:** backend changes, new math.

## AG-03 Formulation-to-Decision pipeline integration test and Decision Studio hookup
**Objective:** one governed flow: ledger READY -> baseline solve -> scenario solves -> Decision Intelligence brief ->
human approval record.
**Baseline:** `solve_run`, `decide`, `brief_json` exist; formulation `record` now provides the lineage root.
**Depends on:** AG-01. **Requirements:** orchestrate via existing public functions only; persist brief with
`record_sha256`; approval endpoint records approver and brief hash; reject approval if brief hash changed.
**Tests:** E2E with real HiGHS (product mix, expect 2600 baseline, MACH120 scenario 2800). **Out of scope:** modifying
solver, trust or recommendation code; any machine write-back (industrial mode is advisory).

## Roadmap
Claude next: NL extractor contract (LLM proposals with quotes), unit-aware dimensional checks, bound-conflict evidence.
Antigravity: AG-01 -> AG-02/AG-03 in parallel. User decisions: auth model for actor identity; storage backend.
