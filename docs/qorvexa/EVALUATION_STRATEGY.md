# QORVEXA — Evaluation Strategy (Week 1 specification)

This document specifies **how** retrieval, diagnosis and the digital twin will be measured. It does not report new results.

**Implementation status (2026-11-16, branch `fix/hermetic-test-settings`, uncommitted):**

| Part | Status |
|---|---|
| Retrieval Recall@K, Hit Rate@K, macro averaging (§A.4) | **Implemented** as pure functions in `knowledge/eval.py`, unit-tested on toy data. Precision@K, graded nDCG and citation/grounding/abstention metrics for the new benchmark: **specified only**. |
| Diagnosis evidence, contradiction, recommendation and HITL scoring rules (§B.3) | **Implemented** as pure functions in `anomaly/rca_eval.py`, unit-tested on toy data. Inputs for recommendation class and HITL flags: **pending** (D-10). |
| Twin MAE / RMSE / skill vs persistence and train-mean (§C.1) | **Implemented** in `twin/physics.py` (existing). |
| Twin prediction intervals, coverage, width, interval score, regime breakdown (§C.2) | **Specified only.** The twin computes no interval. (The conformal-style p-values in `anomaly/calibration.py` calibrate anomaly scores; they are not twin prediction intervals.) |
| Harness foundation (§D): v0 JSONL schemas, strict case loader, lenient prediction loader, RCA scoring, retrieval runner, deterministic results writer, CLI, CI validation step | **Implemented (Task 02B, uncommitted)** in `backend/src/factorymind/evaluation/`, with tests in `tests/test_evaluation_harness.py`. |
| Starter benchmark sets | **Implemented, small:** 16 RCA cases, 13 retrieval cases in `backend/benchmarks/qorvexa/`. Not statistically powered; no real manuals; no dev/test split. |
| Full §A.2 / §B.1 schemas (bilingual fields, author, licence, dev/test split), bootstrap CIs, evidence-package generator for synthetic scenarios, RCA engine runs on MetroPT-3 from the CLI | **Specified only / pending.** |

Any future component (neural embeddings, rerankers, LLMs, new twin models) is accepted only if it beats the baselines below **on the same frozen evaluation sets**.

General rules:
1. **Freeze before you tune.** Each evaluation set is split into `dev` (≤ 40 %) and `test` (≥ 60 %). Thresholds and prompts are tuned on `dev` only. `test` is run once per candidate release and its results are reported.
2. **Version everything.** Sets are stored as JSONL with a `schema_version`, a SHA-256 manifest and a changelog. They stay in Git when the sources' licences allow it, otherwise in private storage referenced by hash.
3. **Provenance.** Every item records its author, its source documents and `origin ∈ {real, translated, synthetic}`. Synthetic and translated items are reported separately from real ones.
4. **Uncertainty.** Every metric is reported with a 95 % bootstrap CI (2,000 resamples over items). For time-series metrics (twin) items are not independent, so the resampling unit is a time block (§C.2.1).
5. **Reproducibility.** One command per evaluation, deterministic seeds, and the results JSON saved under `artifacts/evaluations/<suite>/<date>-<git_sha>/`.

---

## A. Retrieval benchmark (bilingual industrial)

### A.1 Corpus rules
- **Eligible:** industrial documents only (manuals, SOPs, datasheets, maintenance records, dataset descriptions).
- **Excluded from the benchmark corpus:** the project's own design docs (`docs/m*.md`), which are currently indexed.
- Each document records `doc_id, title, language, licence, permission_ref, page_count, sha256`.

### A.2 Item schema (`retrieval_items.jsonl`)
```json
{
  "id": "RQ-0001",
  "split": "dev|test",
  "language": "en|ar|mixed",
  "origin": "real|translated|synthetic",
  "category": "terminology|component|sensor|procedure|failure_mode|troubleshooting|numeric_spec|multi_passage|insufficient_evidence",
  "query": "…",
  "gold_passages": [{"doc_id": "…", "page": 12, "span": "exact quoted text", "grade": 2}],
  "answerable": true,
  "reference_answer": "…",
  "required_facts": ["…"],
  "author": "…",
  "reviewed_by": "…"
}
```
`grade`: 2 = directly answers, 1 = partially relevant, 0 = irrelevant. `insufficient_evidence` items have `answerable=false` and no gold passages.

### A.3 Coverage targets (minimum, test split)
| Category | EN | AR | Mixed |
|---|---|---|---|
| terminology / component / sensor | 12 | 8 | 4 |
| procedure / troubleshooting | 12 | 8 | 4 |
| failure_mode | 6 | 4 | 2 |
| numeric_spec | 8 | 4 | 2 |
| multi_passage | 6 | 4 | 2 |
| insufficient_evidence | 8 | 6 | 2 |
| **Total** | **52** | **34** | **16** (≈ 100 items) |

Arabic items that are translations must carry `origin=translated`, and native-Arabic results must be reported separately.

### A.4 Metrics
Notation, per item *q*: `Rel(q)` = set of gold passages with grade ≥ 1, identified by `doc_id + page` (+ span id when a page holds several gold spans); `Ret_K(q)` = the first K **distinct** passages returned (duplicates count once). All ranking metrics are **macro-averaged** over the eligible items of a split (each item weighs equally), reported per language and origin stratum.

| Metric | Per-item definition | Eligible items / zero handling |
|---|---|---|
| Recall@K (K = 1, 3, 5, 10) | \|Ret_K(q) ∩ Rel(q)\| / \|Rel(q)\| | answerable items with \|Rel(q)\| ≥ 1. Items with \|Rel(q)\| = 0 are **excluded** (0/0 is undefined, never scored 0 or 1); they are measured by abstention correctness. Empty results score 0. |
| Hit Rate@K (K = 1, 3, 5, 10) | 1 if \|Ret_K(q) ∩ Rel(q)\| ≥ 1, else 0 | same as Recall@K. Reported separately: for `multi_passage` items it awards 1 when only one of several required passages is found, so it must not be read as recall. |
| Precision@K | \|Ret_K(q) ∩ Rel(q)\| / K | same as Recall@K; the denominator is K even when fewer than K passages are returned. |
| MRR@10 | 1 / rank of the first relevant passage in the top 10, 0 if none | same as Recall@K |
| nDCG@K (K = 5, 10) | DCG@K / IDCG@K with DCG@K = Σᵢ₌₁ᴷ (2^gradeᵢ − 1) / log₂(i + 1); IDCG@K is the DCG of the gold passages sorted by grade | same as Recall@K (IDCG > 0 for every eligible item) |

Reference implementation for Recall@K and Hit Rate@K: `knowledge/eval.py` (`recall_at_k`, `hit_rate_at_k`, `macro_recall_at_k`, `macro_hit_rate_at_k`), tested in `tests/test_evaluation_metrics.py`. **Caveat:** the existing API field `recall_at_5` returned by `evaluate_retrieval_modes` is a Hit Rate@5; it is unchanged because renaming it is an API contract change.
| Citation correctness | share of cited passages whose `doc_id+page` match a gold passage **and** whose quoted span is found verbatim in the source |
| Grounded answer accuracy | share of answers containing all `required_facts` with no unsupported claim (rubric-scored by 2 reviewers; Cohen's κ reported) |
| Abstention correctness | precision/recall of abstention on `answerable=false` vs answerable items; out-of-domain false-answer rate |

### A.5 Baseline
- **System:** the current `HybridRagIndex` (BM25 + TF-IDF/TruncatedSVD + RRF + relevance gate), measured in the modes `bm25_only`, `dense_svd_only` and `hybrid_rrf`, with configuration frozen at the commit evaluated.
- **The existing `knowledge/eval.py` set (11 cases) is kept as a regression test only.** It is not discriminative (identifier-copied queries, broad relevance sets).
- **Acceptance for a new retriever:**
  - it beats the best baseline mode on test-split nDCG@5 with the bootstrap CI of the difference excluding 0;
  - it does not degrade abstention correctness;
  - it meets the latency budget (P95 ≤ 2.5 s, NFR-02).
- The proposal's "Recall@5 ≥ 0.88" is a target to evaluate, not a current fact.

---

## B. Diagnosis evaluation

### B.1 Case schema (`diagnosis_cases.jsonl`)
```json
{
  "case_id": "DX-0001",
  "origin": "real|synthetic",
  "source": "metropt3:F3 | fault_injection:flatline:TP3 | partner:WO-…",
  "asset_id": "metropt3-apu-1",
  "window": {"start": "…", "end": "…"},
  "input_observations": ["telemetry window ref", "human observations"],
  "available_evidence": ["load_duty", "unloaded_tp3_slope", "…"],
  "expected_hypotheses": [{"category": "air_leakage", "acceptable_rank_max": 2}],
  "unacceptable_hypotheses": ["sensor_malfunction"],
  "expected_supporting_evidence": ["load_duty"],
  "expected_contradicting_evidence": [],
  "expected_missing_checks": ["air_demand_flowmeter"],
  "expected_uncertainty": "abstain|low|moderate",
  "expected_recommendation_class": "inspect|monitor|gather_evidence|no_action",
  "human_approval_required": true,
  "ground_truth_level": "operator_report|verified_outcome|synthetic_label"
}
```

### B.2 Initial case sources (available now)
| Source | Cases | Ground truth level |
|---|---|---|
| MetroPT-3 F1–F4 alarm episodes | 4 | operator_report (coarse, single class) |
| MetroPT-3 `pca_spe` warning episodes (unlabelled) | 34 | expectation = "not air_leakage" or "abstain" (weak) |
| Fault injection via `anomaly/faults.py` (stuck/offset/flatline/drift/noise × channels) | ~30 | synthetic_label |
| Normal windows (negative controls) | ~20 | no fault expected |

Real partner work orders, when obtained, become a separate `real` stratum.

### B.3 Metrics
- Top-1 / top-2 hypothesis agreement with expected categories (per stratum).
- Unacceptable-hypothesis rate.
- Abstention correctness on `expected_uncertainty = abstain` cases.
- Evidence, contradiction, recommendation and HITL metrics, defined below.

**Matching rule.** `expected_supporting_evidence`, `expected_contradicting_evidence` and `expected_missing_checks` contain **canonical keys**, the same identifiers the RCA engine emits as `EvidenceReference.signal_or_feature` (e.g. `load_duty`, `unloaded_tp3_slope`, `air_demand_flowmeter`). Keys match exactly after trimming and case-folding; free-text or semantic matching is not used. A valid key is a lowercase identifier (`[a-z0-9_][a-z0-9_.:-]*` after normalisation). **Ground-truth keys are strict:** a blank, non-string or malformed expected key (or critical contradiction kind) raises an error, since it is a case-file defect. **System-produced keys are lenient:** blank, non-string or malformed surfaced keys are discarded, so they never match, are not counted in precision denominators, and cannot crash a batch. Duplicates count once. The surfaced sets are taken from the **primary (rank-1) hypothesis**: its `supporting_evidence`, `contradicting_evidence` and `missing_evidence` respectively.

For case *c*: E = expected keys, S = surfaced keys (both as sets).

| Metric | Per-case formula | Zero denominator |
|---|---|---|
| Supporting-evidence recall | \|E_sup ∩ S_sup\| / \|E_sup\| | E_sup = ∅ → not applicable (N/A) |
| Supporting-evidence precision | \|E_sup ∩ S_sup\| / \|S_sup\| | S_sup = ∅ → N/A (the omission is already counted by recall); E_sup = ∅ with S_sup ≠ ∅ scores 0 |
| Missing-check recall (disclosure) | \|E_miss ∩ S_miss\| / \|E_miss\| | E_miss = ∅ → N/A |
| Missing-check precision | \|E_miss ∩ S_miss\| / \|S_miss\| | S_miss = ∅ → N/A; E_miss = ∅ with S_miss ≠ ∅ scores 0 |
| Contradicting-evidence disclosure | \|E_con ∩ S_con\| / \|E_con\| | E_con = ∅ → N/A |
| Recommendation accuracy | 1 if predicted class = `expected_recommendation_class` (exact, case-folded), else 0. Does **not** include HITL. | never N/A (every case has an expected class) |
| HITL compliance | 1 if the output **requires** human approval **and** has no automated execution path, else 0. It does not show that approval was **obtained**. Reported separately over all outputs; target 100 %. | either input unknown → undecided (counted separately; never reported as compliant) |

**Critical-contradiction metrics** *(proposed definitions, D-11)*. Let C = set of `ContradictionRecord.kind` values with `severity="critical"` in the case's evidence package, and D = set of `signal_or_feature` keys cited anywhere in the RCA assessment. A kind *k* counts as disclosed if D contains *k* or one of its aliases (`sensor_freeze` ↔ `stale_telemetry`, the key the engine uses). All three metrics are N/A when C = ∅.

| Metric | Per-case formula | Undecided |
|---|---|---|
| Contradiction disclosure | 1 if **every** k ∈ C is disclosed, else 0. Partial disclosure scores 0. | never (only N/A when C = ∅) |
| Safe diagnostic disposition | **1** if `abstained = true` or primary category = `insufficient_evidence`. Otherwise **1** only if R = true (recommendation labelled safe) **and** there is a basis: primary category ∈ {`telemetry_inconsistency`, `sensor_malfunction`}, or V = true (documented contradiction-directed verification action). **0** if R = false, or for a mechanical diagnosis (`air_leakage`, `abnormal_high_demand`, `concurrent_conditions`) with V = false. **A category alone never establishes safety**, because an unsafe operational recommendation can coexist with it. The confidence band is not an input. | any other combination, including `abstained` unknown, category unknown, R unknown, or a mechanical diagnosis with V unknown |
| Contradiction handling | Disclosure **and** safe disposition. Disclosure alone never passes; a hidden or partially disclosed contradiction scores 0 whatever the disposition. | disclosure = 1 and disposition undecided |

Undecided results are reported as their own count, never folded into pass or fail. Until inputs R and V have a source (see below), report handling both as measured and as a conservative lower bound that treats undecided as 0.

Aggregation: macro mean over the cases where the metric is applicable, reported **with the count of applicable and N/A cases** per stratum. If no case in a stratum is applicable the metric is reported as N/A, never as 0 or 1. Reference implementation: `anomaly/rca_eval.py` (`evidence_recall`, `evidence_precision`, `critical_contradictions_disclosed`, `safe_diagnostic_disposition`, `critical_contradiction_handled`, `recommendation_accurate`, `hitl_compliant`) with `knowledge/eval.py:macro_average`; toy and adversarial tests in `tests/test_evaluation_metrics.py`.

**Implemented vs available inputs.** The scoring functions above are implemented and tested. Which inputs the current RCA output can supply:

| Input | Available from `RcaAssessment` / evidence package today? |
|---|---|
| Critical contradiction kinds (C) | **Yes**: `EventEvidencePackage.quality.contradictions[*]` with `severity == "critical"` |
| Disclosed keys (D) | **Yes**: `signal_or_feature` of every `EvidenceReference` in `ranked_hypotheses` |
| `abstained`, primary category | **Yes**: `RcaAssessment.abstained`, `RcaAssessment.primary_category` |
| Recommendation-safety label (R) | **No.** RCA output has no structured operational recommendation; `VerificationStep.action` is free text, and safety is not inferred from text. Needs an agreed structured source (D-10). Until then every non-abstaining output under a critical contradiction is undecided or unsafe, never safe. |
| Contradiction-directed verification action (V) | **No.** `VerificationStep` has `step_id`, `priority` and free-text `action` but no link to a contradiction; matching the text is not allowed. Needs a labelled mapping or a schema field (D-10). |
| Predicted recommendation class | **No** (see below, D-10) |
| HITL flags | **No** (see below, D-10) |

Details of the unavailable inputs:
- **Predicted recommendation class.** `RcaAssessment` has no recommendation-class field (it has `abstained`, ranked hypotheses and `verification_steps` with a priority). A documented mapping to `inspect | monitor | gather_evidence | no_action` must be agreed (D-10) before recommendation accuracy can be measured. No mapping is assumed here.
- **HITL flags.** There is no `human_approval_required` field on RCA output. HITL is currently enforced structurally: RCA is advisory, has no actuation path, and decisions go through the human incident/decision workflow (`api/incidents.py`). Until a per-output flag exists, `hitl_compliant` returns undecided for RCA outputs, and the structural properties are checked by code review and existing tests. Neither shows that a human actually approved a given output; that is recorded only in the incident/decision audit trail.
- **Support scores are reported as ranks/indices only.** They are not converted to percentages and not called confidence. Calibration analysis (reliability diagrams) is deferred until ≥ 50 real labelled outcomes exist.

### B.4 Baseline
The existing `anomaly/rca.py` engine through `rca_eval.py`. Report per stratum; the 4 real cases are too few for statistical claims, so they are reported individually.

---

## C. Digital twin evaluation

### C.1 Existing methodology [implemented, `twin/physics.py`]
- Fit on pre-2020-04-01 normal telemetry; validate on ≥ 2020-04-01 contiguous normal segments, excluding ±24 h around failure reports.
- Horizons 10 / 30 / 60 / 120 / 240 min; teacher-forced (observed load state) and closed-loop (simulated governor).
- Baselines: persistence (last observed value) and constant training-mean TP3.
- Metrics: TP3 MAE / median MAE / RMSE, skill vs each baseline, duty-cycle MAE, cycle-count error, oil-temperature MAE.
- Support rule: a horizon is supported only if it beats both baselines and all shorter horizons are supported.

### C.2 Additions required (specification, not yet implemented)
| Metric | Definition | Status |
|---|---|---|
| Prediction interval (PI) | 90 % PI per horizon and lead time, calibrated **on a chronologically earlier calibration split only**, using block-level conformal scores (§C.2.1) | missing |
| Interval coverage | share of held-out points inside the PI, per horizon, per rolling-origin fold and pooled; CI from a block bootstrap (§C.2.1) | missing |
| Mean interval width | mean (upper − lower) in bar; reported next to coverage | missing |
| Interval score | Winkler / interval score (penalises width and misses) | missing |
| Regime breakdown | all metrics split by loaded / unloaded / mixed segment start state | missing |
| Failure cases | the 10 worst segments per horizon, with timestamp, error and regime, listed in the report | missing |
| Violation audit | definition and breakdown of `constraint_violations` (currently 1711) | done (`BASELINE_STATUS.md` §5): all teacher-forced lower-bound clamps at 120 / 240 min in parked windows; 0 closed-loop |

### C.2.1 Interval methodology for autocorrelated residuals
TP3 rollout residuals are strongly autocorrelated within a segment, depend on the load regime and drift over months. Standard split-conformal assumes exchangeable calibration and test scores; individual 10 s residuals are not exchangeable, so pooling them would give intervals whose nominal 90 % means nothing. The method is therefore specified as follows.

1. **Chronological three-way split, no shuffling.** fit (< 2020-04-01, as today) → calibration (the earliest part of the held-out period) → test (everything later). An **embargo** of at least the longest horizon (240 min) separates consecutive splits, so that no rollout window crosses a boundary. The ±24 h exclusion around failure reports is kept.
2. **One score per block, not per point.** The nonconformity score at lead time *h* is |TP3_sim(h) − TP3_obs(h)| from one rollout. Calibration uses at most one rollout per **block**, where a block is a contiguous run of time at least as long as the residual decorrelation length. The block length is **measured, not assumed**: it is the smallest lag at which the autocorrelation of calibration residuals falls inside the 95 % white-noise band, rounded up to whole hours (one calendar day is the fallback if that lag exceeds 24 h). The measured ACF and the chosen block length are reported. Overlapping rollouts from the same block are never used as separate calibration points.
3. **Conformal quantile.** For n calibration blocks the PI half-width is the ⌈(n + 1)(1 − α)⌉-th smallest score (α = 0.10). A horizon with n < 19 calibration blocks gets no PI and is reported as "insufficient calibration data".
4. **Rolling-origin evaluation.** Test origins t₁ < t₂ < … step forward through the test period (e.g. weekly). For fold *k*, calibrate on all blocks before t_k minus the embargo and evaluate on [t_k, t_{k+1}). Coverage and width are reported per fold, per horizon and per regime (loaded / unloaded / mixed start state), and pooled across folds. This exposes drift instead of averaging it away.
5. **Adaptive variant (optional, compared, not assumed better).** Adaptive Conformal Inference (Gibbs & Candès, 2021), which updates α online from observed misses, is evaluated on the same rolling-origin folds. It is adopted only if it improves the stability of per-fold coverage without materially widening intervals.
6. **Uncertainty on the metrics.** The CI of coverage, width and skill uses a **block bootstrap** (resampling whole blocks or days, 2,000 resamples), not an i.i.d. bootstrap over points or overlapping segments. This overrides general rule 4 for twin metrics.
7. **Claims.** Under autocorrelation and regime shift the finite-sample guarantee of split-conformal does **not** hold. Only the measured coverage on later test folds is reported; no "guaranteed 90 % coverage" claim is made. With roughly four months of held-out data, a few dozen blocks per horizon is the realistic ceiling, so the per-regime results will be noisy and are reported with their block counts.

### C.3 Acceptance criteria (proposed, per horizon)
A horizon/mode is **supported** only if all of these hold:
1. MAE beats persistence and train-mean (as today), with the block-bootstrap CI of the skill excluding 0.
2. 90 % PI coverage, pooled over the rolling-origin test folds (§C.2.1), is within [0.85, 0.95]; per-fold coverage is reported alongside.
3. Mean PI width is narrower than that of the train-mean baseline's empirical 90 % band at the same horizon, so that coverage is not bought with uninformative intervals.
4. No regime stratum with ≥ 20 segments has skill < 0.

**Current status:** criterion 1 holds at 10 and 30 min closed-loop and up to 60 min teacher-forced. Criteria 2–4 are **unmeasured**. The proposal's "MAE ≤ 0.08 bar at 30 min" is not supported by evidence (current 0.49 bar) and should be replaced by these criteria.

### C.4 Behaviour preservation
This task changes nothing in twin behaviour. A later change to reject unsupported horizons (HTTP 422) is an API contract change and needs explicit approval (see `DECISIONS_REQUIRED.md`).

---

## D. Evaluation harness (Task 02B foundation, implemented)

Code: `backend/src/factorymind/evaluation/`. Data: `backend/benchmarks/qorvexa/` (see its `README.md`). Tests: `backend/tests/test_evaluation_harness.py`. The harness is offline only: no API route imports it, and it adds no dependencies.

### D.1 Architecture
| Module | Role |
|---|---|
| `schemas.py` | pydantic models: `RcaCase`, `RetrievalCase` (ground truth, strict, `extra="forbid"`), `RcaPrediction` (system output, lenient), `CaseResult` and `MetricOutcome` (results) |
| `jsonl.py` | `load_cases` (strict: any bad line, bad key or duplicate `case_id` rejects the file, with line numbers), `load_rca_predictions` (lenient: broken JSON, schema errors and duplicates become per-case or orphan errors), deterministic `write_jsonl` / `write_json` |
| `rca.py` | `score_rca_case(s)` using the 02A functions in `anomaly/rca_eval.py`; `prediction_from_assessment` (real `RcaAssessment` to prediction, no labels added); `predictions_from_service` (runs the existing engine on cases with an `event_window`; needs MetroPT-3 data) |
| `retrieval.py` | `run_retrieval_case(s)` over an injected `RetrievalSystem`; `system_from_index` wraps an in-process `HybridRagIndex` (no API or auth path). Hit Rate@K and Recall@K reuse `knowledge/eval.py`. |
| `runner.py` | `write_run`: `results.jsonl`, `summary.json`, `manifest.json` (input SHA-256s, system, orphan errors). No timestamps, so identical inputs give byte-identical files (tested). |
| `__main__.py` | `python -m factorymind.evaluation validate / rca / retrieval` |

### D.2 Commands (run from `backend/`)
```
uv run python -m factorymind.evaluation validate <cases.jsonl>
uv run python -m factorymind.evaluation rca --cases <cases.jsonl> --predictions <predictions.jsonl> --out <dir>
uv run python -m factorymind.evaluation retrieval --cases <cases.jsonl> --out <dir> [--raw-dir <data/raw>] [--mode hybrid_rrf|bm25_only|dense_svd_only]
```
Exit code 1 means the case file failed strict validation. Write run outputs under `D:/FactoryMind/artifacts/evaluations/<suite>/<label>/`, not into the repository.

### D.3 Record schemas (v0)
**`rca_case/v0`** (ground truth). For each label, `null` means not labelled (the metric is reported `unlabelled`), and `[]` means labelled as nothing expected (the metric is `not_applicable`).
```json
{"schema_version":"rca_case/v0","case_id":"adv-missing-evidence","task":"rca","provenance":"adversarial_contract",
 "description":"Two required missing checks; ...","input_ref":null,"event_window":null,
 "ground_truth_status":"labelled","ground_truth_level":"contract_fixture",
 "expected_categories":null,"unacceptable_categories":[],"expected_abstain":null,
 "expected_supporting_evidence":["load_duty","unloaded_tp3_slope"],"expected_contradicting_evidence":null,
 "expected_missing_checks":["air_demand_flowmeter","pre_event_baseline"],"critical_contradictions":[],"notes":""}
```
- `provenance` is one of `metropt3_observed` (needs `event_window`), `synthetic_scenario` or `adversarial_contract` (needs `ground_truth_level: contract_fixture`).
- `ground_truth_status` is one of `labelled`, `partial` or `insufficient`. `insufficient` forbids `expected_categories` and `expected_abstain`.
- `ground_truth_level` is one of `operator_report_coarse`, `expert_authored`, `physics_authored`, `contract_fixture` or `none`.
- Keys must be canonical identifiers (§B.3 matching rule). `critical_contradictions` must be `ContradictionRecord.kind` values.
- There is deliberately no recommendation-class field (D-10 is pending).

**`rca_prediction/v0`** (system output). Key lists accept anything; malformed keys are discarded and counted. A wrong type or unknown category invalidates only that case's record.
```json
{"schema_version":"rca_prediction/v0","case_id":"adv-safe-abstention","producer":"contract-fixture/v0","abstained":true,
 "primary_category":"insufficient_evidence","confidence_band":"abstained","supporting_evidence":[],
 "contradicting_evidence":[],"missing_evidence":[],"cited_evidence":["stale_telemetry"],"package_critical_contradictions":null}
```
- C (critical contradictions) comes from the case if labelled; otherwise from `package_critical_contradictions`, which the engine adapter fills from the evidence package; otherwise it is `unlabelled`. The source is recorded in `details.critical_contradictions_source`.

**`retrieval_case/v0`**
```json
{"schema_version":"retrieval_case/v0","case_id":"ret-pca-spe-tiers","task":"retrieval","provenance":"project_document_retrieval",
 "query":"pca_spe non-leak statistical warning tier separation","asset_id":"metropt3-apu-1","language":"en",
 "relevant_chunk_prefixes":["m2-wp2","passport:model-pca_spe"],"notes":"..."}
```
- `provenance` is one of `project_document_retrieval`, `dataset_document_retrieval` or `out_of_scope_probe`. Only out-of-scope probes have, and must have, no prefixes.
- The relevant set is every chunk in the evaluated corpus whose id starts with one of the prefixes. If there is none, the status is `no_relevant_in_corpus` and the metrics are `unavailable`.

**`eval_result/v0`** (one per case, in `results.jsonl`)
```json
{"schema_version":"eval_result/v0","case_id":"adv-critical-mentioned-ignored","task":"rca","provenance":"adversarial_contract",
 "status":"scored","errors":[],"discarded_keys":0,
 "details":{"producer":"contract-fixture/v0","primary_category":"air_leakage","abstained":false,"confidence_band":"moderate",
            "critical_contradictions":["valve_contradiction"],"critical_contradictions_source":"case"},
 "metrics":{"critical_contradiction_disclosure":{"value":1.0,"state":"scored","reason":null},
            "critical_contradiction_handling":{"value":null,"state":"undecided","reason":"R/V labels not provided by RCA output (pending D-10, D-11)"},
            "recommendation_accuracy":{"value":null,"state":"unavailable","reason":"RCA output has no recommendation class (pending D-10)"}}}
```
- `status` is one of `scored`, `missing_prediction`, `invalid_prediction` or `no_relevant_in_corpus`.
- Metric `state` is one of `scored`, `not_applicable`, `unlabelled`, `undecided`, `unavailable` or `no_prediction`.

### D.4 Interpreting outputs
- `summary.json` gives each metric's **mean over `scored` outcomes only** plus a count of every state, overall and per `provenance`. Undecided, unlabelled, missing and invalid results are never averaged in and never counted as success. Report the state counts next to every mean. For a conservative figure, treat `undecided` and `no_prediction` as failures.
- **RCA metrics:** `category_match`, `unacceptable_hypothesis` (lower is better), `abstention_correct`, supporting-evidence recall and precision, missing-check recall and precision, contradicting-evidence disclosure, critical-contradiction disclosure / safe disposition / handling, and `recommendation_accuracy` and `hitl_compliance` (always `unavailable` until D-10 is decided).
- **Without R/V labels**, safe disposition and handling can only be `scored` for abstentions or failed disclosure; otherwise they are `undecided`.
- **Retrieval metrics:** `hit_rate_at_5`, `recall_at_5`, `recall_at_10` (true chunk recall; its ceiling is K/|relevant|), `reciprocal_rank_at_10`, and `out_of_scope_abstention`. The last is index-level only: the index returns nothing when the query has no corpus tokens, and copilot scope gating is not evaluated.

### D.5 CI
`pytest` runs `tests/test_evaluation_harness.py`, which includes the RCA CLI smoke run on the fixture predictions, a determinism check and a retrieval CLI run without the dataset. In addition, `.github/workflows/ci.yml` runs `validate` on both starter case files. Neither needs external data or services.

### D.6 Not yet implemented
- Bootstrap CIs.
- Dev/test split and the frozen-set manifest/changelog (general rules 1-2).
- §A.2 bilingual and licence fields.
- Evidence-package generator for `synthetic_scenario` cases.
- A CLI path that loads `AnomalyService` and runs the engine on MetroPT-3 (the function `predictions_from_service` exists).
- Precision@K, graded nDCG, citation and grounding metrics.
- Twin evaluation inside the harness. Twin metrics remain in `twin/physics.py`.

### D.7 Provenance and the real-data RCA run (Task 03A, implemented)
**Commands**
```
uv run python -m factorymind.evaluation rca-run --cases benchmarks/qorvexa/rca_cases.v0.jsonl --out D:/FactoryMind/artifacts/evaluations/rca-real/<label> [--data-dir D:/FactoryMind/data]
uv run python -m factorymind.evaluation verify <run_dir> [--check-code]
```
`rca-run` loads the current model bundle (integrity-checked by the existing loader), runs the existing evidence-package builder and RCA engine on every case that has an `event_window` (cases without one are listed under `cases_not_run`, not scored), scores the predictions, and writes `predictions.jsonl` and `evidence_provenance.jsonl` next to the usual results. A failure in one case is recorded as an `invalid_prediction` for that case only. Exit code 3 means the data or model is unavailable.

**Deterministic artifacts** (byte-identical for identical inputs and code): `results.jsonl`, `summary.json`, `manifest.json`, `predictions.jsonl`, `evidence_provenance.jsonl`. The manifest records:
- input files (role, path, SHA-256);
- the run configuration;
- a **code fingerprint** (SHA-256 over every `.py` file in the package) and component hashes for the evidence-package builder, RCA engine, RCA scoring, anomaly service and harness. The engine and builder carry no version constants, so their source hashes are their version; nothing was added to production code;
- the dataset (SHA-256 of `telemetry.parquet`, hash of its manifest, and the source-archive SHA-256 *as declared* there; the archive itself is not re-hashed, so `archive_sha256_verified` is `false`);
- the model bundle (version, SHA-256 of `model.joblib`, whether it matches the model card, SHA-256 of the card);
- an `interpretation` block stating the evaluation roles and that no held-out evidence exists.

**Environment metadata** goes to `run_info.json` only: git SHA, branch, dirty flag and dirty paths, Python and platform, creation time (UTC), and the SHA-256 of every deterministic output. If git is unavailable the SHA and dirty flag are `null` with a reason, never "clean".

`verify` recomputes every recorded hash (inputs, dataset, model artifact, outputs) and exits 1 on any mismatch or missing file. `--check-code` also requires an unchanged code fingerprint; it is opt-in because any later edit to the package changes it.

**Evaluation roles.** `rca_case/v0` has an optional `evaluation_role` (`development`, `held_out`, `unspecified`; default `unspecified`). The schema rejects `held_out` for `metropt3_observed` cases. The four F1-F4 cases are `development` (audit below). Each result's `details.evaluation_role` carries the role.

**Audit: did F1-F4 influence the RCA engine?** Evidence found in the repository:
- `docs/m2-wp4-explainable-rca.md` describes F1 as a design case ("genuine co-occurring integrity faults (e.g., F1) activate `concurrent_conditions`") and reports engine output on F1-F4.
- `docs/m2-wp3-event-evidence-foundation.md` reports the evidence-package statistics for F1-F4 during development.
- The engine's thresholds (`_HIGH_DUTY_THRESHOLD`, `_LEAK_DECAY_SLOPE_MAX`, `_MIN_DIAGNOSTIC_SUPPORT`, etc.) have no documented derivation, and the history is one squashed commit, so tuning on F1-F4 can be neither shown nor excluded.
- The existing `evaluate_service_report_rca` derives its expected categories from the package's own stale-telemetry state, which is circular.
- The anomaly detector itself was trained on Feb-Mar 2020 windows, before F1 (April), but that says nothing about the RCA rules.

**Conclusion:** F1-F4 are development cases. They must not be presented as held-out evidence.

**First real run (2026-11-17, uncommitted tree).** The four cases all produced predictions; the engine output matches the values already recorded in `docs/m2-wp4-explainable-rca.md`:

| Case | Primary category | Confidence band | Evidence package | Critical contradictions |
|---|---|---|---|---|
| F1 | `concurrent_conditions` | low | degraded | `sensor_freeze` (2 critical, 4 warning); disclosed; handling `undecided` |
| F2 | `air_leakage` | moderate | sufficient | none |
| F3 | `air_leakage` | low | degraded | none |
| F4 | `air_leakage` | moderate | degraded | none |

All four agree with the coarse label (`air_leakage` or `concurrent_conditions`). **This is not a diagnostic accuracy result:** n = 4, one fault class, day-level operator labels, no repair record, a label that accepts two categories, and development cases. Evidence-key labels, abstention and recommendation/HITL metrics are `unlabelled` or `unavailable`. The run only shows that the adapter and provenance work on real data.

