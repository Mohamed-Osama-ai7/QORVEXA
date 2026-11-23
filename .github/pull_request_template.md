## Summary & Objective

<!-- Clearly describe the problem this PR solves, the mathematical or engineering justification, and the expected outcome. -->

---

## Workstream Alignment

- [ ] **Workstream 1 — Technical Lead & Core Intelligence Architecture** (`@Mohamed-Osama-ai7`)
- [ ] **Workstream 2 — Advanced RAG & Retrieval Engineering** (`@a7medessam196`)
- [ ] **Workstream 3 — Advanced Optimization Engineering** (Mohamed Adel)
- [ ] **Workstream 4 — Causal Intelligence & Industrial Analytics** (Beshoy)
- [ ] **Workstream 5 — Advanced Multi-Agent Orchestration & Governance** (Ziad)
- [ ] **Workstream 6 — Digital Twin, Simulation, Industrial Integration & Observability** (Ali Motassem)

---

## Changed Files & Interface Scope

- **Primary Subsystems Modified:** `[e.g., backend/src/qorvexa/knowledge/]`
- **Interfaces / Shared Contracts Touched:**
  - [ ] No shared contracts touched (pure subsystem-internal changes).
  - [ ] Shared contract modified (requires mandatory review and approval by Technical Lead `@Mohamed-Osama-ai7`).
  - *Details if shared contract changed:* `[e.g., CanonicalLp dataclass added new dual_bound field]`

---

## Verification Evidence & Quality Gates

### Automated Test Execution
- **Backend Tests:** `uv run pytest --basetemp=../tmp_pytest -q`
  - Pass count: `[e.g., 1,924 passed in X.XXs]`
- **Frontend Tests:** `npm test -- --fileParallelism=false --maxWorkers=1`
  - Pass count: `[e.g., 75 passed across 20 files]`
- **Linting & Formatting:**
  - [ ] `uv run ruff check .` passed (0 issues).
  - [ ] `uv run ruff format --check .` passed (clean).
  - [ ] `npm run lint` (`biome check .`) passed (0 issues).
- **Static Type Analysis:**
  - [ ] `uv run mypy src/qorvexa/` passed (0 issues).
  - [ ] `npm run typecheck` (`tsc -b`) passed (0 issues).

### Benchmark Evidence (Where Applicable)
- [ ] Evaluation audit passed: `uv run python -m qorvexa.evaluation audit --strict`
- [ ] Benchmark suite executed: `[e.g., rca_cases.v1 or retrieval_cases.v1]`
- Metrics / Evidence Summary: `[e.g., MRR: 0.92, Recall@5: 0.88]`

---

## Security, Safety & Tenant Isolation

- [ ] **Physical OT Safety Boundary:** I affirm that this PR contains **zero** actuation commands, write-back calls, or socket control pathways to physical industrial equipment (PLCs, DCS, SCADA, RTUs).
- [ ] **Tenant / Factory Isolation:** Factory-scoped data, terminology, and fact ledgers maintain strict database and identifier separation.
- [ ] **Secret Hygiene:** No credentials, API tokens, passwords, or secrets are committed.

---

## Integration Impact & Limitations

- **Downstream Module Impact:** `[Describe any impact on consuming services or UI]`
- **Known Limitations or Deferred Work:** `[Disclose edge cases or unhandled bounds honestly]`
- **Screenshots / Log Excerpts:**
  ```text
  [Paste relevant terminal output, telemetry log, or attach UI screenshot]
  ```
