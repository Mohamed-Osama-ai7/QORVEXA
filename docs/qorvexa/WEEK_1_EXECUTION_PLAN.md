# QORVEXA — Week 1 Execution Plan

## Done in this task
- Baseline verified: `main` @ `6351d4f`, CI green, 613 backend tests collected, 70 frontend tests passing (single worker).
- Capability status, twin and retrieval measurements recorded in `BASELINE_STATUS.md`.
- Data inventory and acquisition plan recorded in `DATA_INVENTORY.md`.
- Evaluation specifications recorded in `EVALUATION_STRATEGY.md`.
- Task 02 review remediation (F-01…F-06), uncommitted on `fix/hermetic-test-settings`: retrieval and diagnosis metric functions with toy tests, twin violation audit, interval methodology spec, teardown-safe test isolation, UI label "Hit rate@5". Local results in `BASELINE_STATUS.md` §2.1.

## Remaining Week 1 tasks

| # | Task | Output | Depends on | Owner (suggested) |
|---|---|---|---|---|
| 1 | Make backend tests hermetic (ignore ambient `.env`) — **implemented** on branch `fix/hermetic-test-settings` (commit `9003a87` + uncommitted teardown fix and regression tests); not yet reviewed or merged | small test-config change + PR | D-6 approved | Platform/QA |
| 2 | Run the full backend suite locally with `--basetemp` on a drive with free space (C: is full), and in CI; record exact pass/fail — **local run done** (`BASELINE_STATUS.md` §2.1); CI run on the remediated branch pending | CI log reference in `BASELINE_STATUS.md` | — | Platform/QA |
| 3 | Start data acquisition (manuals, telemetry, Arabic references) | request emails / NDA drafts, permission log | D-1, D-2 | Data/Asset lead |
| 4 | Draft bilingual glossary v0 (≥ 200 compressor terms) | `glossary.csv` (term_en, term_ar, variants, source) | — | LLM/RAG lead + Arabic reviewer |
| 5 | Implement evaluation harness skeletons (loaders, metrics, bootstrap CI, result writer) with **no** model changes — **partial (Task 02B, uncommitted)**: schemas, strict/lenient JSONL loaders, RCA and retrieval runners, deterministic result writer, CLI, CI validation and starter sets done; bootstrap CI not started | `backend/evaluation/` (proposed) + tests | approval to add code (D-7) | each track owner |
| 6 | Build diagnosis case set v0 from available sources (MetroPT-3 F1–F4, warnings, fault injection, normals) | `diagnosis_cases.jsonl` v0 | 5 | ML/Twin lead |
| 7 | Twin: specify the conformal PI method and investigate `constraint_violations = 1711` — **done as specification and audit** (`EVALUATION_STRATEGY.md` §C.2.1, `BASELINE_STATUS.md` §5); intervals themselves are not implemented | design note | — | ML/Twin lead |
| 8 | Retrieval: write first 30 items **once at least one real manual is available**; otherwise public-docs-only pilot set | `retrieval_items.jsonl` v0 | 3 | LLM/RAG lead |
| 9 | Workstation guidance (free space on C: / pytest `--basetemp` on D:, single-worker vitest) | README section | — | Platform/QA |

## Team workstreams (five members)

| Member | Workstream | Week 1 focus | Interface owned |
|---|---|---|---|
| 1 | Industrial data & assets | acquisition, permissions log, DQS fault-injection plan | data inventory, ingest contracts |
| 2 | ML / time series / twin | diagnosis cases v0, twin PI design, violation audit | `diagnosis_cases.jsonl`, twin eval report |
| 3 | LLM / RAG / KG | glossary, retrieval set, harness | `retrieval_items.jsonl`, retrieval eval report |
| 4 | Decision & UX | diagnosis rubric, HITL invariant tests, responsive-layout fix plan | decision rubric, UI provenance badges spec |
| 5 | Platform / security / QA (tech lead) | hermetic tests, CI baseline, harness scaffolding, review | CI, evaluation result format |

## Critical blockers
1. **No real manuals or Arabic documents.** The retrieval benchmark and every bilingual requirement are blocked until at least one source is obtained (D-1, D-2).
2. **No real compressor data beyond MetroPT-3.** All "factory" claims are blocked.
3. **Local backend suite needs `--basetemp` off C:.** Drive C: is full, so pytest's default temp directory fails with `No space left on device` (earlier attributed to memory, which was wrong). With `--basetemp=D:\FactoryMind\pytest-tmp` it runs locally; CI remains the reference run.
4. **Decisions D-1…D-12 are pending** (D-6 is implemented but awaits review; see `DECISIONS_REQUIRED.md`).

## Week 2 entry criteria
- [x] Full backend suite result recorded from CI or a capable machine (exact counts) — local run, `BASELINE_STATUS.md` §2.1.
- [ ] Hermetic-test fix merged (or explicitly deferred).
- [ ] At least one real English manual with recorded permission, **or** a signed-off decision to run a public-docs-only benchmark.
- [ ] Evaluation harness skeleton merged, with metrics unit-tested on toy data. (Foundation exists, uncommitted; bootstrap CIs and dev/test split do not.)
- [ ] Diagnosis case set v0 frozen (dev/test split, manifest hash).
- [ ] Baseline numbers for retrieval (on the new set, if available) and twin (existing metrics) recorded under `artifacts/evaluations/`.
- [ ] Decisions D-1…D-5 answered.
