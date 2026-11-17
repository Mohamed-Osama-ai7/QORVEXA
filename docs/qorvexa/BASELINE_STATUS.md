# QORVEXA — Baseline Status (Week 1)

*Verified 2026-11-16. Repository: `Mohamed-Osama-ai7/FactoryMind-CDT` (QORVEXA is the working product name; the codebase is still named FactoryMind).*

Legend: **[V]** verified in this task · **[I]** existing implementation (code inspected) · **[P]** proposed / not implemented · **[U]** unverified assumption.

## 1. Repository state [V]

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `6351d4f0ad7c5028b06346386609069be7720d4a` (= `origin/main`) |
| Working tree | clean except the new `docs/qorvexa/` files from this task (uncommitted). Local, git-ignored demo config exists: `backend/.env`, `frontend/.env.local`. |
| Contents vs proposal baseline | proposal cites `ce31304`; `6351d4f` adds PR #20 (frontend demo fixes only). |
| CI on `6351d4f` | GitHub check runs **backend: success, frontend: success** (queried via the public API). |
| Open PRs | #3 (docs, untouched by policy). |

## 2. Test baseline

| Suite | Result | Evidence / notes |
|---|---|---|
| Backend — collection | **613 tests collected** [V] | `pytest --collect-only -q` (local `.env` moved aside during the run, then restored). |
| Backend — execution in CI | **pass** [V] | CI check run `backend: success` on `6351d4f`. The authoritative full run is CI. |
| Backend — local execution | **passes with `--basetemp` on D:** [V] | *Corrected diagnosis (review finding F-03).* The local failures were caused by drive `C:` being full (0 MB free), not by memory: pytest writes `tmp_path` data under `%TEMP%` on `C:`, which fails with `[Errno 28] No space left on device`. The earlier attribution to Windows commit charge / `MemoryError` was wrong. Run locally with `uv run pytest --basetemp=D:\FactoryMind\pytest-tmp` (see §2.1 for the result). Free space on `C:` also affects other tools that write to `%TEMP%`. |
| Backend — local test hermeticity | **fixed on `fix/hermetic-test-settings`** [V] | Tests used to read `backend/.env` via `Settings(env_file=".env")`; a developer `.env` made 2 tests fail. The autouse `_hermetic_settings` fixture (`tests/conftest.py`, `tests/settings_isolation.py`) disables `.env`, hides `FACTORYMIND_*` variables and clears the settings cache before and after every test; regression tests in `tests/test_settings_isolation.py`. |
| Frontend | **18 files / 70 tests passed** [V] | `npx vitest run --maxWorkers=1`. Default parallel workers crash on this machine (`exit 0xC0000409`, resource exhaustion). |
| Lint / type / build | CI green [V]; locally last verified 2026-11-12 (ruff, mypy strict, biome, tsc, vite build). |

### 2.1 Local backend run after Task 02 remediation [V]
2026-11-16, branch `fix/hermetic-test-settings` (base `9003a87` plus uncommitted remediation changes), `backend/.env` present on disk:
- `uv run pytest --basetemp=D:\FactoryMind\pytest-tmp -q`: **661 passed**, 0 failed, 133.27 s (613 before plus 48 new tests in `test_settings_isolation.py` and `test_evaluation_metrics.py`).
- Frontend: `npx vitest run --maxWorkers=1 src/features/knowledge/KnowledgeExplorer.test.tsx` 4 passed; `npx biome check src` clean (62 files); `npx tsc --noEmit` clean. The full frontend suite was not re-run.
- `uv run ruff check .`: all checks passed. `uv run ruff format --check .`: 115 files already formatted.
- `uv run mypy` (strict, CI configuration): no issues in 75 source files.

## 3. Architecture snapshot [I]

- **Backend:** FastAPI modular monolith, 13 packages, ~19.8k LOC, 10 routers, 34 endpoints (`backend/src/factorymind/api/*`). Persistence: SQLAlchemy 2 + **SQLite** with append-only triggers; no migrations (`db/session.py` raises `SchemaMismatchError` on version change).
- **Frontend:** React 19, Vite, TanStack Router/Query, Tailwind 4, ECharts; orval-generated client from `frontend/openapi.json`; routes `/ /assets /assets/$assetId /anomaly /incidents /incidents/$incidentId /knowledge /twin /copilot /edge /models /audit /auth/callback`.
- **API contract:** `frontend/openapi.json`, drift-checked by `tests/test_openapi_contract.py` and CI.
- **Auth:** OIDC resource server (`auth/tokens.py`), 5 roles (viewer, engineer, approver, auditor, admin), permission-based `require` / `require_data_source`, separation of duties (creator ≠ decider; admin cannot approve). Single data-owner tenant (`data_source_tenant_id`). Verified end-to-end with a local OIDC provider on 2026-11-12.
- **Audit & decisions:** per-tenant SHA-256 hash chain (`db/audit.py`), bounded retry on contention, append-only incidents → decisions (`confirm/dismiss/defer`, optimistic concurrency) → observations → outcome verification.

## 4. Capability status

| Capability | Status | Evidence |
|---|---|---|
| Auth, RBAC, SoD, audit chain | GREEN [V] | `auth/*`, `db/audit.py`, `tests/test_auth_*`, `test_audit_contention.py`, CI |
| HITL decision & outcome workflow | GREEN [V] | `api/incidents.py`, `tests/test_decision_workspace_api.py` |
| MetroPT-3 offline ingestion & data passport | GREEN (batch only) [I] | `data/metropt3.py`, `data/foundation.py` |
| Live / edge ingestion (OPC-UA, MQTT, Historian) | RED [I] | descriptors + URL policy + batch preflight only; `contracts/connectors.py` → `connector_io = "not_implemented"`; no protocol client in `pyproject.toml` |
| Data quality | AMBER [I] | regime integrity gating (stale / valve contradiction) + evidence completeness; **no DQS score or quarantine** |
| Anomaly detection | AMBER [I] | `duty_rule` primary, `pca_spe` warning tier; evidence = 4 operator reports (LOEO 4/4, ≈0.03 FA/valid day) |
| RCA | AMBER [I] | `anomaly/rca.py`; outputs a **support index, explicitly not a calibrated probability** |
| Hybrid retrieval | AMBER [V] | `knowledge/rag.py`: BM25 + **TF-IDF/TruncatedSVD (LSA)** + RRF + content-relevance abstention; **no neural embeddings, no vector DB, no reranker** |
| Knowledge graph | AMBER [I] | hand-coded nodes/edges in `knowledge/kg.py` |
| Copilot | GREEN, deterministic only [V] | `copilot/service.py` routes RCA through `analyze_event_evidence`; `LlmAdapter` protocol is not wired to any provider |
| AI4I multi-model benchmark | AMBER (synthetic benchmark) [V] | HGB test ROC-AUC 0.9803, PR-AUC 0.7984, Brier 0.0124, ECE 0.0054 on 2,000 synthetic test rows (`artifacts/benchmarks/ai4i2020/benchmark_report.json`). Not factory evidence. |
| Digital twin | AMBER [V] | see §5 |
| Decision comparison | AMBER [I] | `decision/optimizer.py`, heuristic constants and configurable cost assumptions |
| Multi-LLM, agents, TSFMs, deep learning, Arabic/RTL, BI ledger, observability, Docker/IaC | RED / BLUE [I] | absent from code and dependencies |

## 5. Digital twin — current measured state [V]

Source: `GET /api/v1/twin/model` on the running local backend (model `apu-mass-balance-v2`, fitted < 2020-04-01, validated ≥ 2020-04-01 on normal segments).

| Horizon | Segments | Closed-loop TP3 MAE (bar) | Persistence MAE | Train-mean MAE | CL skill vs train-mean | Status |
|---|---|---|---|---|---|---|
| 10 min | 227 | 0.328 | 1.535 | 1.001 | +0.67 | supported |
| 30 min | 191 | **0.492** | 1.834 | 0.666 | +0.26 | supported |
| 60 min | 168 | 0.601 | 1.877 | 0.582 | −0.03 | **unsupported (CL)**; teacher-forced only |
| 120 min | 146 | 0.658 | 1.922 | 0.542 | −0.22 | unsupported |
| 240 min | 136 | 0.669 | 1.953 | 0.528 | −0.27 | unsupported |

- The supported limits are computed (30 min closed-loop, 60 min teacher-forced, 30 min duty).
- The API accepts horizons 10–240 min and **labels** unsupported runs rather than rejecting them. The proposal's HTTP 422 requirement is not yet met.
- **No prediction intervals exist**, so interval coverage cannot be measured yet.
- The validation reports `constraint_violations = 1711`. **Breakdown (review finding F-02, reproduced 2026-11-16 by instrumenting `_clamp` / `_rollout_observed_segment` during `CompressorReservoirTwin.fit_from_telemetry` on `data/processed/metropt3/telemetry.parquet`; no Twin code changed):**

  | Horizon | Segments | Closed-loop clamps | Teacher-forced clamps |
  |---|---|---|---|
  | 10 / 30 / 60 min | 227 / 191 / 168 | 0 | 0 |
  | 120 min | 146 | 0 | **237** (1 segment) |
  | 240 min | 136 | 0 | **1,474** (2 segments: 957 + 517) |

  - All 1,711 are **lower-bound** clamps (simulated TP3 < 0.0 bar); there are no upper-bound (> 15.0 bar) clamps and **no closed-loop clamps at any tested horizon**.
  - The counter increments once per 10 s step spent at the floor, so a few long windows produce a large number.
  - Only two distinct windows are involved (starting 2020-05-27 13:53 and 2020-06-12 00:55). In both the APU is off (median motor current 0.040 / 0.045 A; observed loaded share 0 % and 3.5 %), i.e. parked/stabled. Observed TP3 stays ≥ 8.17 and ≥ 6.72 bar, but the teacher-forced model applies its constant drain rate (`k_drain`) with the valve forced closed, so simulated pressure reaches 0 bar.
  - Interpretation: an artefact of the constant-drain assumption during long idle periods, at horizons already marked unsupported. It is not a closed-loop instability. Not fixed here (no Twin changes in scope); a pressure-dependent or isolated-reservoir idle model would be a separate, approved change.
- Simulated load cycles are systematically fewer than observed (e.g. 0.80 vs 1.33 per 30-min segment).

## 6. Retrieval — current measured state [V]

- **Corpus:** 61 chunks = 11 passport chunks (generated from code), 3 pages of the MetroPT-3 data description PDF, 47 chunks of the project's own design docs. No OEM manual, SOP or Arabic text.
- **Built-in evaluator** (`knowledge/eval.py`, 11 cases):

  | Mode | Hit Rate@5 (API field `recall_at_5`) | MRR@10 | nDCG@5 |
  |---|---|---|---|
  | bm25_only | 1.00 | 1.00 | 0.792 |
  | dense_svd_only | 1.00 | 1.00 | 0.738 |
  | hybrid_rrf | 1.00 | 1.00 | 0.762 |

  The queries reuse identifiers copied from the corpus and the relevance sets are broad, so this set is **not discriminative**. The earlier audit estimated a random-retrieval hit rate@5 of about 0.69 on the original 8 cases.
  - *Naming caveat (F-01):* `evaluate_retrieval_modes` computes the field `recall_at_5` as a hit rate (≥ 1 relevant chunk in the top 5), not passage recall. Renaming or recomputing it changes the API contract, so the field is unchanged pending decision D-9; the Knowledge page column now reads "Hit rate@5" (label only, no contract change). True Recall@K is available as `knowledge.eval.recall_at_k` / `macro_recall_at_k` for the new benchmark.
- **Out-of-domain abstention:** "What is the capital of France?" and "recipe for chocolate cake" return 0 hits (P0B relevance gate).

## 7. Task 02 completion audit (evaluation infrastructure) [V]

Checked against the code on 2026-11-16. "Implemented" means code plus tests exist; "specified" means only this documentation exists.

| Capability | State | What exists | What is missing |
|---|---|---|---|
| Retrieval metrics | **Partial** | Recall@K, Hit Rate@K, macro averaging with undefined-case handling (`knowledge/eval.py`, toy-tested). Harness runner with reciprocal rank and out-of-scope probes (`evaluation/retrieval.py`). Existing 11-case evaluator (Hit Rate@5, MRR@10, binary nDCG@5) as a regression check. | Precision@K, graded nDCG, citation correctness, grounded-answer and abstention scoring for the new benchmark; real-manual benchmark items (starter set is project-document based, with circularity risk). |
| Diagnosis metrics | **Partial** | Evidence recall/precision, missing-check recall/precision, contradicting-evidence disclosure, critical-contradiction disclosure / safe disposition / handling (separate), recommendation accuracy, HITL compliance as pure functions (`anomaly/rca_eval.py`, toy-tested). Existing casebook harness (category match, citation grounding, correlation disclosure). | Larger, labelled case set (starter set only), an evidence-package generator for synthetic scenarios, the recommendation-class mapping, HITL inputs, and the recommendation-safety and verification-action labels (D-10, D-11), and a CLI run of the engine on MetroPT-3 (`predictions_from_service` exists, untested against real data). Without those labels, safe disposition is undecided for every non-abstaining output facing a critical contradiction. |
| Twin metrics and baselines | **Implemented** (point forecasts) | MAE / median / RMSE, skill vs persistence and train-mean, duty and oil metrics, support rule (`twin/physics.py`). Violation breakdown verified (§5). | Prediction intervals, coverage, width, interval score, regime breakdown, worst-segment report (all specified only, `EVALUATION_STRATEGY.md` §C.2). |
| Dataset / schema contracts | **Partial (Task 02B)** | v0 pydantic schemas and strict/lenient JSONL loaders (`evaluation/schemas.py`, `evaluation/jsonl.py`); starter files `backend/benchmarks/qorvexa/` (16 RCA, 13 retrieval cases) validated in tests and CI. | Full §A.2 / §B.1 fields (bilingual, author, licence), manifests with hashes for the case sets, dev/test splits. |
| Reproducible machine-readable results | **Partial (Task 02B)** | `python -m factorymind.evaluation rca / retrieval` writes `results.jsonl`, `summary.json`, `manifest.json` (input SHA-256s, no timestamps); byte-identical re-runs are tested. The anomaly experiment and the AI4I benchmark also write JSON reports under `artifacts/`. | Bootstrap CIs, git-SHA-labelled run directories, twin results in the harness. The violation-audit script still lives outside the repo (`D:\FactoryMind\verify_twin_violations.py`). |

### 7.1 Task 02B harness foundation (uncommitted) [V]

- Implemented: `backend/src/factorymind/evaluation/` (schemas, JSONL I/O, RCA scoring, retrieval runner, run writer, CLI), `backend/benchmarks/qorvexa/` starter sets, `tests/test_evaluation_harness.py`, a CI step that validates both case files.
- Reuses, does not redefine, the Task 02A metrics. D-10 and D-11 are not assumed: recommendation accuracy and HITL compliance are `unavailable`, and critical-contradiction handling is `undecided` unless the output abstains or disclosure fails.
- Starter data: 4 observed MetroPT-3 report windows (coarse operator label only), 2 synthetic scenarios (no evidence-package generator yet), 10 adversarial contract fixtures, 13 retrieval cases. Small, partly synthetic, not statistically powered; no quality or validity claims.
- Not implemented: bootstrap CIs, dev/test split, bilingual/licence fields, Precision@K / graded nDCG, twin evaluation in the harness (see `EVALUATION_STRATEGY.md` §D.6).

### 7.2 Task 03A: provenance and real MetroPT-3 RCA run (uncommitted) [V]

- Implemented: `evaluation/provenance.py` (git SHA and dirty status, dataset and model hashes, code fingerprint, component hashes, `verify`), the `rca-run` and `verify` commands, per-case failure isolation, the `evaluation_role` field, `tests/test_evaluation_provenance.py` (11 tests).
- Real data: the existing adapter ran on all four MetroPT-3 report windows and produced four predictions and four evidence-provenance records; two runs gave byte-identical deterministic artifacts and `verify --check-code` passed. Details and the F1-F4 development-case audit are in `EVALUATION_STRATEGY.md` §D.7.
- Limits: F1-F4 are development cases; n = 4; no evidence, abstention or recommendation labels; the source archive hash is declared, not re-verified; recommendation accuracy and HITL compliance remain unavailable (D-10), contradiction handling remains undecided where a critical contradiction exists (D-11).
- Environment: another pytest process was using the default `pytest-tmp` directory during this task, which caused file-lock errors (an environment collision, not a code failure); the clean full run used `D:\FactoryMind\pytest-tmp-03a`.

## 8. Known limitations (carry into every claim)

1. No live plant data; MetroPT-3 is a single train APU, Feb–Aug 2020, ~0.1 Hz, with 4 coarse operator reports.
2. AI4I 2020 is synthetic; its metrics are benchmark-only.
3. RCA support ≠ probability; no labelled data exists to calibrate it.
4. Twin is skilful only to 30 min closed-loop; no uncertainty intervals.
5. Retrieval corpus is self-authored; the existing metrics cannot rank retrieval methods.
6. SQLite, single tenant, in-process state (rate limiter, RAG index, twin fit).
7. Tokens are held in memory (page reload re-authenticates); layout is not usable at phone width.
