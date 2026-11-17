# QORVEXA — TASK 02: Independent Engineering Review & Quality Assurance Report

**Reviewer:** Independent Senior Software Engineer / QA Reviewer  
**Date:** 2026-11-16  
**Repository:** `D:\FactoryMind\FactoryMind-CDT` (`Mohamed-Osama-ai7/FactoryMind-CDT`)  
**Target Reviewed:** Claude Code's completed Task 02 implementation & specifications  
**Review Type:** Strict Read-Only Verification & Independent Assessment

---

## 1. Executive Verdict

### Verdict: **APPROVED WITH MINOR FINDINGS**

**Rationale:**
Claude Code's implementation of backend test isolation (commit `9003a879ddb7a23eafc7152d1443f2269652f6d0`) is functionally sound, isolates ambient environment variables and `.env` configuration, preserves production runtime behavior, and achieves 100% test pass rates across both backend and frontend suites.

However, independent engineering review identified five material technical defects and gaps in Claude Code's evaluation strategy, baseline documentation, and root-cause analysis:
1. **[P1 High] Hit Rate mislabeled as Recall@K:** In `docs/qorvexa/EVALUATION_STRATEGY.md` §A.4, Recall@K is mathematically defined as Hit Rate @ K ("share of answerable items with ≥ 1 gold passage in top K"). For multi-passage industrial items, this conflates finding a single passage with retrieving all required source evidence.
2. **[P1 High] Omission of Twin Constraint Root Cause:** Claude documented `constraint_violations = 1711` but deferred investigating the physical cause. Independent simulation proved that all 1,711 violations are minimum pressure clamps (< 0.0 bar) occurring exclusively in teacher-forced simulation at 120 min (237 clamps) and 240 min (1,474 clamps) during long idle parking windows. Closed-loop simulation has **0 violations** across all horizons (10m, 30m, 60m, 120m, 240m).
3. **[P2 Medium] Misdiagnosis of Workstation Test Failure:** Claude reported local test failure as Windows memory exhaustion (commit charge 97.5%). Independent investigation discovered that Windows drive `C:` has **0.00 GB free space**, causing `[Errno 28] No space left on device` when writing temporary files to `%TEMP%`. Running pytest with `--basetemp=D:\FactoryMind\pytest-tmp` executes all 613 tests cleanly in 158.94s.
4. **[P2 Medium] Exchangeability Violation in Conformal Interval Proposal:** The proposed split-conformal prediction intervals for the Digital Twin fail to account for time-series autocorrelation and regime shifts.
5. **[P2 Medium] Asymmetric Fixture Teardown:** The `_hermetic_settings` fixture clears `get_settings` cache at test setup but does not clear it at fixture teardown.

---

## 2. Repository State

- **Current Branch:** `fix/hermetic-test-settings` (tracking `origin/fix/hermetic-test-settings`)
- **Current HEAD Commit:** `9003a879ddb7a23eafc7152d1443f2269652f6d0`
- **Base Main Commit:** `6351d4f0ad7c5028b06346386609069be7720d4a` (= `origin/main`, including merged PR #20)
- **Open Pull Requests:** PR #3 remains open and completely untouched at `19cbd5cba534645916e7fc0c8c5167a80df2683c`.
- **Working Tree Status:**
  - Committed changes on branch:
    - `backend/tests/conftest.py` (+15 lines)
  - Untracked files created by Claude:
    - `docs/qorvexa/BASELINE_STATUS.md` (8,237 bytes)
    - `docs/qorvexa/DATA_INVENTORY.md` (7,601 bytes)
    - `docs/qorvexa/DECISIONS_REQUIRED.md` (2,484 bytes)
    - `docs/qorvexa/EVALUATION_STRATEGY.md` (9,312 bytes)
    - `docs/qorvexa/WEEK_1_EXECUTION_PLAN.md` (3,781 bytes)
  - External audit file created outside repository:
    - `D:\FactoryMind\QORVEXA_Architecture_Audit.md` (28,380 bytes)
  - Note on `TASK_02_REPORT.md`: A single consolidated report titled `TASK_02_REPORT.md` was not created by Claude Code; the required contents were distributed across `docs/qorvexa/*.md` and `QORVEXA_Architecture_Audit.md`.

---

## 3. Files Inspected

| File Path | Inspection Focus | Verification Status |
|---|---|---|
| `backend/tests/conftest.py` | `_hermetic_settings` fixture implementation | Verified (commit `9003a87`) |
| `backend/src/factorymind/config.py` | `Settings` model config, `.env` file resolution, `get_settings()` | Verified |
| `backend/tests/auth_helpers.py` | `AuthKit.settings()`, test settings creation | Verified |
| `backend/src/factorymind/twin/physics.py` | `_clamp()`, `_rollout_observed_segment()`, `total_violations` | Verified |
| `backend/src/factorymind/api/twin.py` | Model endpoints and simulation routes | Verified |
| `backend/src/factorymind/anomaly/rca.py` | RCA hypothesis scoring and evidence collection | Verified |
| `backend/src/factorymind/knowledge/rag.py` | BM25 + LSA + RRF hybrid retrieval and relevance gate | Verified |
| `backend/src/factorymind/knowledge/eval.py` | Built-in retrieval eval cases | Verified |
| `docs/qorvexa/BASELINE_STATUS.md` | Capability status and baseline metrics | Verified |
| `docs/qorvexa/DATA_INVENTORY.md` | Dataset catalog, provenance, and gap analysis | Verified |
| `docs/qorvexa/DECISIONS_REQUIRED.md` | Pending architectural and data decisions | Verified |
| `docs/qorvexa/EVALUATION_STRATEGY.md` | Retrieval, Diagnosis, and Twin evaluation specs | Verified |
| `docs/qorvexa/WEEK_1_EXECUTION_PLAN.md` | Workstream sequencing and entry criteria | Verified |
| `data/processed/metropt3/manifest.json` | Dataset validation and SHA-256 integrity | Verified |
| `data/processed/ai4i2020/manifest.json` | Synthetic benchmark manifest integrity | Verified |

---

## 4. Test Execution Results

All test suites and static analysis tools were executed independently on the workstation:

| Test Suite / Tool | Command Executed | Result | Duration | Notes |
|---|---|---|---|---|
| **Backend Unit Tests** | `uv run pytest --basetemp=D:\FactoryMind\pytest-tmp -q` | **613 passed** / 0 failed | 158.94s | 100% pass rate across entire suite |
| **Backend Anomaly API** | `uv run pytest --basetemp=D:\FactoryMind\pytest-tmp tests/test_anomaly_api.py` | **41 passed** / 0 failed | 14.22s | Isolated from local `.env` |
| **Backend Incidents API** | `uv run pytest --basetemp=D:\FactoryMind\pytest-tmp tests/test_incidents_api.py` | **25 passed** / 0 failed | 24.07s | Isolated from local `.env` |
| **Backend Ruff Linter** | `uv run ruff check .` | **All checks passed!** | 2.12s | 0 warnings, 0 errors |
| **Backend Ruff Formatter** | `uv run ruff format --check .` | **112 files formatted** | 2.51s | Clean formatting |
| **Backend Strict Mypy** | `uv run mypy` | **Success: 0 issues in 75 files** | 4.22s | Strict mode compliant |
| **Frontend Vitest** | `npx vitest run --maxWorkers=1` | **18 files / 70 passed** | 36.84s | Single-worker avoids thread crashes |
| **Frontend Biome** | `npx biome check src` | **Checked 62 files: clean** | 0.09s | No formatting/lint issues |
| **Frontend TypeScript** | `npx tsc --noEmit` | **Clean: 0 errors** | 3.11s | Strict typing verified |
| **Frontend Vite Build** | `npm run build` | **Build succeeded** | 0.98s | Production dist generated |

---

## 5. Review A: Backend Test Isolation Findings

### 5.1 Mechanism Implemented
In `backend/tests/conftest.py`, lines 20–33:
```python
@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read a developer's ``backend/.env`` or ambient ``FACTORYMIND_*`` variables;
    a test that needs a setting passes it explicitly or sets it with ``monkeypatch``."""
    import os

    from factorymind.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.upper().startswith("FACTORYMIND_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
```

### 5.2 Verification Analysis
1. **Hermeticity Scope:** Because `autouse=True` is set on a function-scoped fixture in the root `conftest.py`, every single test in the test suite receives isolation automatically.
2. **Dual-Layer Isolation:**
   - Ambient environment variables prefixed with `FACTORYMIND_` (case-insensitive) are stripped from `os.environ` via `monkeypatch.delenv(name)`.
   - File-based `.env` loading is explicitly suppressed by setting `Settings.model_config["env_file"] = None`.
   - The LRU cache for `get_settings()` is cleared before each test execution via `get_settings.cache_clear()`.
3. **State Restoration:** Pytest's `monkeypatch` fixture automatically restores both deleted environment variables in `os.environ` and modified keys in `Settings.model_config` upon test teardown.
4. **Production Settings Integrity:** In normal application runtime (e.g. `uvicorn`), `Settings.model_config["env_file"] = ".env"` remains active. Verified that outside pytest, `get_settings()` loads `FACTORYMIND_DATA_DIR` and `FACTORYMIND_ASSET_IDS` from `backend/.env`.
5. **Secret Protection:** Ambient secrets or credentials injected via environment variables cannot leak into test logs or assertion diffs because they are unmapped before test execution.
6. **Reproduction Experiment:**
   - Injected dummy ambient variable: `$env:FACTORYMIND_ASSET_IDS='["metropt3-apu-1","dummy-leaked-asset"]'`
   - Verified that `test_missing_model_returns_503_without_paths` in `tests/test_anomaly_api.py` passed with 0 pollution.
   - Verified that `tests/test_incidents_api.py` (25 tests) and `tests/test_anomaly_api.py` (41 tests) passed completely with `backend/.env` present on disk.

### 5.3 Minor Limitation Identified
`_hermetic_settings` does not `yield` to execute `get_settings.cache_clear()` upon fixture teardown. While functional during the test run because subsequent tests clear the cache at setup, session teardown hooks or post-run callbacks could theoretically read cached settings from the final test. (Classified as P3 Low).

---

## 6. Review B: Evaluation Infrastructure Verification

### 6.1 Bilingual Industrial Retrieval Specification
- **Recall@K Flaw (P1 High):**
  In §A.4, Recall@K is defined as: *"share of answerable items with ≥ 1 gold passage (grade ≥ 1) in the top K"*.
  - **Correction:** This formula defines **Hit Rate @ K** (or Success@K), not Recall@K.
  - Standard Recall@K must be defined as:
    $$\text{Recall}@K = \frac{|\text{Retrieved}_K(q) \cap \text{Relevant}(q)|}{|\text{Relevant}(q)|}$$
  - For `multi_passage` items where $|\text{Relevant}(q)| \ge 2$, Hit Rate awards 1.0 even if only 1 out of 3 passages is retrieved, distorting multi-hop retrieval assessment.
- **Precision@K:** Adequately defined as relevant retrieved / $K$, but requires explicit macro-averaging notation across query splits.
- **nDCG@K:** Correctly incorporates graded relevance ($0, 1, 2$), but requires explicit specification of the logarithmic discount formula:
  $$\text{DCG}@K = \sum_{i=1}^K \frac{2^{\text{grade}_i} - 1}{\log_2(i + 1)}$$
- **Abstention & Groundedness:** Correctly includes precision/recall on unanswerable queries (`answerable=false`) and inter-annotator agreement (Cohen's $\kappa$).
- **Baseline Rigor:** Commendably recognizes that the current 11-case `knowledge/eval.py` set is uninformative due to literal identifier overlap, maintaining it strictly as a regression check.

### 6.2 Diagnosis & Root Cause Analysis Specification
- **Schema & Stratification:**
  - `diagnosis_cases.jsonl` provides comprehensive stratification: 4 real MetroPT-3 alarm episodes (F1–F4), 34 `pca_spe` warning episodes, ~30 synthetic fault injection cases (`anomaly/faults.py`), and ~20 negative normal controls.
  - Distinct separation of `origin ∈ {real, synthetic}` and `ground_truth_level ∈ {operator_report, verified_outcome, synthetic_label}`.
- **Support Score Honesty:** Commendably enforces that RCA support scores remain uncalibrated indices/ranks and prohibits presenting them as probability percentages or confidence.
- **HITL Invariant:** Hard assertion that 100% of diagnostic outputs require human approval with no automated execution path.
- **Missing Specification (P2 Medium):** The specification lacks exact string/semantic matching criteria for `expected_missing_checks` and does not define handling for empty supporting evidence sets ($0/0$ division).

### 6.3 Digital Twin Evaluation Specification
- **Baseline Framework:** Robustly structured with per-horizon MAE/RMSE comparisons against persistence and train-mean baselines.
- **Prediction Interval Limitation (P2 Medium):**
  - Section C.2 proposes "split-conformal on rollout residuals per horizon".
  - Time-series compressor data exhibits strong temporal autocorrelation, load-cycle hysteresis, and regime non-stationarity.
  - Standard split-conformal methods assume exchangeability ($i.i.d.$), which produces severely miscalibrated coverage during operational regime transitions.
  - An adaptive time-series conformal prediction method (e.g., Adaptive Conformal Inference or block-bootstrap calibration) must be specified.

---

## 7. Review C: Dataset Integrity Findings

### 7.1 Inventory Verification
Every dataset, archive, and manifest documented in `DATA_INVENTORY.md` was verified on disk:

| Resource Path | Type Claimed | Verified Properties | Disk Hash Verified |
|---|---|---|---|
| `data/raw/metropt3/metropt3.zip` | Real telemetry archive | 218,381,995 bytes | `aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a` |
| `data/raw/metropt3/Data Description_Metro.pdf` | Documentation | 81,208 bytes, 3 pages | Verified present |
| `data/processed/metropt3/telemetry.parquet` | Processed telemetry | 22,032,884 bytes, 1,516,948 rows | 15 sensor channels, 331 gaps >60s |
| `data/raw/ai4i2020/ai4i2020.zip` | Synthetic archive | 522,170 bytes | `f601f14294bcf190f9d720676b7f0aea46a26cde9ab8ebc7b4f8174d9d26b252` |
| `data/processed/ai4i2020/data.parquet` | Synthetic benchmark | 182,302 bytes, 10,000 rows | 6 leakage-free features, 339 failures |
| `data/factorymind-demo.sqlite3` | SQLite demo database | 122,880 bytes | Schema v2, append-only triggers |

### 7.2 Dataset Honesty & Anti-Fabrication Check
- **No Fabricated Data:** Verified that no fake plant data, fabricated Arabic manuals, or synthetic incident tickets were introduced.
- **Strict Separation:** AI4I 2020 is clearly identified as a synthetic benchmark dataset and prohibited from being cited as factory compressor evidence.
- **Explicit Missing Assets:** Missing assets (real factory compressor telemetry, OEM service manuals, Arabic technical corpora, maintenance work orders) are honestly logged as missing with realistic acquisition paths.

---

## 8. Review D: Twin Constraint Audit Assessment

### 8.1 Investigation Findings
Claude documented `constraint_violations = 1711` in baseline documents but did not analyze its root cause. The independent reviewer executed a full audit of `CompressorReservoirTwin` on held-out MetroPT-3 normal telemetry ($\ge$ 2020-04-01).

The exact breakdown across validation horizons and simulation modes:

```
Total validation violations reported: 1,711

Horizon  10m (227 segments): Closed-Loop = 0 | Teacher-Forced =    0 | Total =    0
Horizon  30m (191 segments): Closed-Loop = 0 | Teacher-Forced =    0 | Total =    0
Horizon  60m (168 segments): Closed-Loop = 0 | Teacher-Forced =    0 | Total =    0
Horizon 120m (146 segments): Closed-Loop = 0 | Teacher-Forced =  237 | Total =  237
Horizon 240m (136 segments): Closed-Loop = 0 | Teacher-Forced = 1474 | Total = 1474
--------------------------------------------------------------------------------------
Grand Total:                 Closed-Loop = 0 | Teacher-Forced = 1711 | Total = 1,711
```

### 8.2 Physical & Mathematical Root Cause
1. **Clamp Boundary:** All 1,711 violations are **lower boundary clamps** ($p < 0.0$ bar). Zero upper boundary clamps ($p > 15.0$ bar) occurred.
2. **Closed-Loop Governor Invariant:** Closed-loop simulation experienced **0 violations across all horizons**. The simulated governor correctly cuts in at $p \le 8.2$ bar and cuts out at $p \ge 10.0$ bar, maintaining pressure strictly within physical bounds.
3. **Teacher-Forced Depletion During Idle Regimes:**
   In teacher-forced simulation (`closed_loop=False`), valve state is forced by observed telemetry: $u_{\text{step}} = u_{\text{obs}}$.
   On long contiguous validation windows (120 min and 240 min) during train stabling or parking, the APU is turned off ($u_{\text{obs}} = 0$ continuously).
   The ODE model updates pressure as:
   $$\Delta p = -k_{\text{drain}} \cdot \Delta t$$
   Because the model assumes a constant linear drain rate without pressure-dependent decay or check-valve isolation, simulated pressure drains linearly to 0.0 bar after ~90 minutes of continuous idle.
   Once $p_{\text{sim}} \le 0.0$ bar, the physical clamp `_clamp(raw_p, 0.0, 15.0)` clamps the pressure to 0.0 bar.
   **Every single 10-second step at the floor increments the violation counter by 1.**
4. **Conclusion:** The count of 1,711 is an artifact of step-level accumulation in teacher-forced mode on parked trains at horizons that are already marked unsupported ($\ge 120$ min). It does not indicate instability or failure of the closed-loop physical model.

---

## 9. Review E: Scope Compliance

Task 02 strictly adhered to architectural boundaries and project scope:
- **No Premature Database Migration:** SQLite with append-only triggers remains the database engine (`backend/src/factorymind/db/session.py`). No PostgreSQL or TimescaleDB was added.
- **No Vector DBs:** Hybrid RAG continues to use in-memory BM25 + SVD/LSA + RRF. No pgvector, Chroma, Qdrant, or Neo4j dependencies were introduced.
- **No Heavy ML/DL Packages:** `pyproject.toml` and `package.json` dependencies remain identical to `origin/main`. No PyTorch, Transformers, or BGE-M3 dependencies were added.
- **No External LLM Wiring:** `ControlledCopilot` remains deterministic with local rule tools; no external LLM APIs were configured.
- **No API Contract Changes:** `frontend/openapi.json` is identical to `origin/main`.
- **Security Boundaries Preserved:** Role-based access control, cryptographic token verification, and hash-chained audit logging were kept strictly intact.

---

## 10. Findings Classified by Severity

| ID | Severity | Category | Summary of Finding | Location |
|---|---|---|---|---|
| **F-01** | **P1 High** | Evaluation Metric | Recall@K defined as Hit Rate @ K (find $\ge 1$ passage), distorting multi-passage retrieval evaluation. | `docs/qorvexa/EVALUATION_STRATEGY.md:56` |
| **F-02** | **P1 High** | Digital Twin | Root cause of 1,711 twin constraint violations was deferred rather than analyzed and explained. | `docs/qorvexa/BASELINE_STATUS.md:71` |
| **F-03** | **P2 Medium** | Test Diagnosis | Workstation test failures attributed to memory/commit charge instead of full `C:` disk space (%TEMP%). | `docs/qorvexa/BASELINE_STATUS.md:24` |
| **F-04** | **P2 Medium** | Statistical Rigor | Split-conformal prediction intervals proposed without addressing time-series autocorrelation and regime shifts. | `docs/qorvexa/EVALUATION_STRATEGY.md:135` |
| **F-05** | **P2 Medium** | Evaluation Metric | Missing checks and supporting evidence metrics lack explicit formulas for empty ground truth sets. | `docs/qorvexa/EVALUATION_STRATEGY.md:112` |
| **F-06** | **P3 Low** | Code Cleanliness | `_hermetic_settings` fixture lacks symmetric `get_settings.cache_clear()` on teardown. | `backend/tests/conftest.py:20` |
| **F-07** | **P3 Low** | Deliverables | New `docs/qorvexa/*.md` files remain uncommitted in the working tree. | Working tree status |

---

## 11. Required Corrections for Claude Code

Claude Code must apply the following corrections prior to closing Week 1:

1. **Fix Recall@K Formula in `EVALUATION_STRATEGY.md`:**
   - Rename the existing metric to `HitRate@K` (or `Success@K`).
   - Define `Recall@K` as macro-averaged $\frac{|\text{Retrieved}_K \cap \text{Relevant}|}{|\text{Relevant}|}$ over answerable items.
2. **Update Constraint Violation Analysis in `BASELINE_STATUS.md`:**
   - Incorporate the findings from Review D: state explicitly that closed-loop simulation has 0 violations across all horizons, and that the 1,711 violations occur solely in teacher-forced mode at 120m (237) and 240m (1,474) due to unmodeled linear drain during long stabling/parking periods.
3. **Correct Workstation Guidance in `BASELINE_STATUS.md`:**
   - Note that local test execution requires setting `--basetemp` to a drive with free space (e.g. `D:\FactoryMind\pytest-tmp`) due to 0 GB free space on drive `C:`.
4. **Refine Time-Series Conformal Interval Specification:**
   - Specify adaptive conformal inference (ACI) or block-bootstrap calibration on non-exchangeable time series residuals.
5. **Add Teardown Cache Clear in `conftest.py`:**
   - Update `_hermetic_settings` to yield and clear the settings cache on teardown:
     ```python
     @pytest.fixture(autouse=True)
     def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
         ...
         get_settings.cache_clear()
         yield
         get_settings.cache_clear()
     ```
6. **Commit Documentation Deliverables:**
   - Stage and commit the `docs/qorvexa/` specification files to the task branch.

---

## 12. Final Recommendation

The engineering foundations delivered in Task 02 are solid. The test hermeticity fix solves a real operational problem, and the data inventory and evaluation strategy provide an honest, unvarnished baseline. 

Once Claude Code incorporates the mathematical and documentation corrections outlined above, Task 02 will be fully closed and ready for merge into `main`. Task 03 should proceed following this sign-off.
