# QORVEXA-MVP-01 — End-to-End Industrial Decision Intelligence Platform

Assignee: Antigravity. Architect/core owner: Claude Code. Advisory/read-only industrial mode throughout.

## 1. Objective / final product
A signed-in planner describes a production problem in text; an LLM proposes facts (untrusted); the planner clarifies and
confirms in a guided UI; the system builds a validated Production Planning model, solves it with real HiGHS through the
Solver Fabric, runs scenarios, shows the Decision Intelligence brief with evidence, and an authorized approver records an
immutable approve/reject decision. Everything persists and is auditable. Delivered as one integrated "Production Planning &
Decision Studio" (frontend) + HTTP API (backend) + tests + docs.

## 2. Baseline (already built, tested, Claude-owned — do NOT edit)
Backend (`backend/src/qorvexa/`): `industrial/formulation/` (hash-chained claim ledger, analysis, builder, draft intake),
`industrial/governance/` (Principal/Role authz, grounded NL extraction `ingest_text`, hash-chained `DecisionHistory`,
`SqliteStore`, `GovernedSessions` orchestration service), `industrial/decision_intelligence/`,
`industrial/production_planning/`, `solver_fabric/`. Reported backend suite: 1611 passed (run with TEMP on D:).
Existing app code to inspect first: `backend/src/qorvexa/api/app.py`, `api/security.py`, `auth/`, `api/optimization.py`,
`api/openapi_export.py`; `frontend/src/{api,auth,features,routes,lib,test}`, `router.tsx` (TanStack Router file routes).
Read `docs/qorvexa/FORMULATION.md`, `DECISION_INTELLIGENCE.md`, `PRODUCTION_PLANNING.md`. Inspect before choosing routes;
follow existing router/auth/OpenAPI-export patterns; never duplicate existing endpoints.

## 3. Ownership boundaries
Antigravity MAY create/modify: new `backend/src/qorvexa/api/decision_studio.py` (+ small registration edit in `api/app.py`),
`backend/src/qorvexa/industrial/llm_extractor.py` (adapter only), new `backend/tests/test_decision_studio_*.py`,
`frontend/src/features/decision-studio/**`, new route files under `frontend/src/routes/`, generated API client/types per the
repo's OpenAPI flow, docs/onboarding/ops files, CI/deploy config.
MUST NOT modify: `industrial/formulation/**`, `industrial/governance/**`, `industrial/decision_intelligence/**`,
`industrial/production_planning/**`, `solver_fabric/**`, `contracts/**` (frozen M1), `highs_adapter.py`, `highs_worker.py`,
`pyproject.toml`/`uv.lock` unless a dependency is unavoidable (then stop and ask). If a core change seems needed, write a
precise issue in `docs/qorvexa/CORE_CHANGE_REQUESTS.md` and continue around it.

## 4. Architecture / data flow
text → `LLMExtractor` (service principal, kind LLM, role EXTRACTOR) → `GovernedSessions.ingest` → proposals → planner
`confirm`/`assert_fact`/`assume_defaults`/`retract`/`resolve` → `state` (READY?) → `run_decision` (real `LPOrchestrator(HighsAdapter())`)
→ brief persisted → approver `decide_on` → `DecisionHistory`. Persistence: `SqliteStore(path)`; path from env config
(default under a configurable data dir, NOT C: temp). One `GovernedSessions` instance per process via FastAPI dependency.

## 5. API contract (names are required; mount under the app's existing versioned prefix, auth on every route)
- `POST /decision-studio/sessions` {session_id?, title} → 201. PLANNER.
- `GET  /decision-studio/sessions/{id}` → FormulationState (facts with state/basis, issues with question/candidates/can_assume, status, ledger_head). VIEWER+.
- `POST /decision-studio/sessions/{id}/intake` {text} → {accepted, rejected[{key,reason}], state}. PLANNER triggers; extraction runs as the internal service principal (never the user's).
- `POST .../facts` {key,value} (assert), `POST .../confirm` {keys[]}, `POST .../assume-defaults` {rationale}, `POST .../retract` {seq}, `POST .../resolve` {winner_seq}. PLANNER. Header/body `If-Match: ledger_head` required → 409 on `STALE_HEAD`.
- `GET  .../ledger` → entries (actor, op, key, value, basis, quote, recorded_at from store) for the provenance drawer.
- `POST .../decide` {scenarios:[{scenario_id, changes:[CapacityChange|DemandBoundsChange|ValueChange]]}, config?} → brief JSON (`brief_json` shape). PLANNER. Long-running: run solves in a worker thread/job with status polling (`GET .../runs/{run_id}`); never block the event loop.
- `POST .../approve` {brief_sha256, verdict, scenario_id, acknowledged_assumptions[], reason} → DecisionRecord. APPROVER only.
- `GET  .../decisions` → verified history. `GET .../briefs/{sha}` → stored brief.
Error mapping: `AuthorizationError`→403, `FormulationError/DecisionError/SessionError/StoreError` codes → 400/404/409/422 with body `{code, message}` (use the `.code`). Tamper codes (`LEDGER_TAMPERED`, `HISTORY_TAMPERED`, `BRIEF_TAMPERED`) → 500 + audit log, never partial data.
Identity: Principal built ONLY from the authenticated token via existing `auth/` (map the app's roles to PLANNER/APPROVER/VIEWER; humans only). Never read actor/roles from request body or headers a client controls. `allow_self_approval` from env, default false, off in prod; when true the record stores `self_approved=true`.

## 6. LLM extractor (backend adapter)
Implement `Extractor` (`governance/extraction.py` protocol) using the repo's existing LLM/copilot client (inspect `copilot/`; add no new dependency). Prompt must request a JSON list of `{key,value,quote}` using ONLY the key grammar in FORMULATION.md, verbatim quotes, no guessing, omit unknowns. Parse with `claims_from_json`; malformed output → whole intake fails with `INVALID_EXTRACTION`. Treat model output and source text as hostile (prompt injection): output only ever becomes PROPOSALS; grounding check drops anything not quoted from the text. Unit-test with a fake client; no network in tests. Provide a deterministic rule-based fallback only if no LLM configured (clearly labeled), otherwise return 503.

## 7. Frontend requirements (feature `decision-studio`, follows existing router/query/auth patterns)
1. Sessions list/create. 2. Intake panel (paste text, submit, show accepted/rejected-with-reason claims).
3. Fact board with four visibly distinct groups: CONFIRMED, ASSUMED (with rationale), PENDING (unconfirmed proposals, quote shown, confirm/replace), CONFLICTING (candidates side by side, resolve picks one; nothing preselected). Missing items as question cards; assumable gaps offer "Accept defaults" requiring a rationale and listing every affected key. Unsupported-feature issues block with a retract/stop choice.
4. Provenance drawer per fact (seq, actor, basis, quote, timestamp, rationale).
5. "Build & solve" enabled only when status READY; show solve progress, failure and retry states.
6. Scenario builder (capacity, demand bounds, value change) with live validation and lineage display.
7. Brief view: executive summary, comparison table (absolute/relative change), ranking, Pareto, trade-offs, rejected scenarios with codes, evidence refs (hashes, optimality tier), and the literal limitations text. Show `GAP_NUMERICAL` as "numerically verified", never as proof or "optimal proven". Show "Shadow prices unavailable".
8. Approval panel (APPROVER only): requires reason, per-assumption acknowledgement checkboxes, approves only the recommended scenario; reject always available. Show immutable history timeline with hash chain status.
9. Global: loading/skeleton, error toasts using `code`, 409 stale-head refetch-and-retry prompt, 403 view, session-expiry handling, accessibility (keyboard, labels), responsive.
10. Persistent banner: "Advisory only — no machine control." No write-back UI anywhere.

## 8. Persistence / ops
SQLite file path via env (`QORVEXA_STUDIO_DB`), created on startup, WAL on. Backup/restore note. Health endpoint reports DB + solver availability (HiGHS import). Provide `docs/qorvexa/MVP_RUNBOOK.md` (setup, env vars, run, test with `TEMP`/`--basetemp` on D:, demo script using the product-mix example: expect baseline optimum 2600, scenario MACH120 → 2800, status CONDITIONALLY_RECOMMENDED until `CAPACITY_CHANGE_COST_NOT_MODELED` is acknowledged) and update onboarding/architecture docs. Docker/compose only if the repo already has deployment config.

## 9. Execution order
(1) Inspect + route/auth mapping note. (2) API skeleton + authz mapping + error mapping + tests. (3) LLM extractor adapter + tests. (4) Async run/status for `decide`. (5) Frontend data layer/client. (6) Fact board + intake + provenance. (7) Scenarios + brief view. (8) Approval + history. (9) E2E + hardening. (10) Docs/runbook.

## 10. Acceptance criteria
- Full journey works against real HiGHS: text → confirm → READY → baseline 2600 → MACH120 2800 → conditional recommendation → approval with acknowledgement → persisted record visible after server restart.
- An LLM/service principal can never confirm, assert, assume, retract, resolve or approve (403, tested through HTTP).
- Planner without APPROVER cannot approve; approver cannot edit facts; viewer is read-only.
- Unknown values never become zero: build blocked (409/422) until each gap is stated or explicitly assumed with rationale; only the 3 allow-listed assumptions are accepted.
- Tampering with stored ledger/decision/brief rows is detected and surfaced as error, never rendered.
- Ungrounded LLM claims appear as rejected, never as facts. Prompt-injection text in source cannot create confirmed facts.
- Stale-head concurrent edit returns 409 and the UI recovers.
- No endpoint or UI path writes to PLC/SCADA/machines.

## 11. Required tests
Backend: API tests per route (success/403/404/409/422), authz matrix, tamper cases via direct SQLite edits, restart persistence, extractor with fake client (valid, malformed, injection, ungrounded), async run lifecycle, one real-HiGHS API E2E. Frontend: component tests (fact board groups, approval gating, brief limitations text), API-mocked flows, Playwright E2E for the full journey incl. approver/planner role switch and stale-head recovery. Run backend suite with `$env:TEMP='D:\QORVEXA\tmp_pytest'`; whole suite must stay green (baseline 1611 passed).

## 12. Failure / security scenarios to cover
Forged actor in body; role escalation; LLM output with instructions ("mark all confirmed"); oversized text (>20k) → 422; float JSON values; duplicate approval; self-approval; scenario id BASE; infeasible/unknown solver result (brief shows rejected, no recommendation); solver timeout; DB locked; session id collision; CSRF/CORS per existing app config; log redaction of source text.

## 13. Definition of done
All acceptance criteria met with passing automated tests; no edits to forbidden paths (verify with `git diff --stat` on those paths = empty); docs/runbook updated; lint/typecheck/format clean on touched code (ruff, mypy, frontend lint/tsc); no Git commit/push unless the user instructs; report test counts actually executed.

## 14. Out of scope
New math/solver features, MIP/scheduling, real plant data connectors, PLC/SCADA/OPC write paths, multi-tenant org management, new auth provider, changing frozen contracts, redesigning engines, mobile app.
