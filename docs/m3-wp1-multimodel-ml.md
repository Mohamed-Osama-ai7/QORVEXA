# M3-WP1 (`WP-C2`) — Multi-Model ML Engine, Uncertainty Quantification & AI4I 2020 Benchmark

M3-WP1 adds a specialized multi-model ML orchestrator, Ledoit-Wolf Mahalanobis out-of-distribution (OOD) detection, probability calibration metrics, model promotion governance gates, conservative abstention, and a controlled UCI **AI4I 2020** predictive-maintenance classification benchmark — kept strictly separated from real MetroPT-3 operational compressor metrics.

---

## 1. Uncertainty, OOD Detection & Abstention (`factorymind.ml`)

- **`MahalanobisOodDetector` (`uncertainty.py`):** Fits Ledoit-Wolf shrinkage precision matrices over training numerical feature distributions and calibrates a 99.5th-percentile distance gate; flags non-finite or shifted inputs (`is_ood=True`).
- **`MultiModelOrchestrator` (`orchestrator.py`):** Routes single-sample requests cleanly and enforces mandatory conservative abstention rules yielding the canonical message `"Insufficient evidence to make a reliable diagnosis"` (`ABSTENTION_MESSAGE`):
  1. `asset_mismatch` / `unknown_model_id`: blocks cross-asset or unregistered-model predictions on uncalibrated machines (`asset_id != "ai4i-milling-bench"`).
  2. `data_quality_degraded` / `data_quality_insufficient` / `invalid_product_type` / `corrupt_or_non_finite` / `out_of_range`: blocks degraded, invalid, or non-finite physical telemetry before inference.
  3. `ood_mahalanobis_exceeded`: refuses to predict outside multivariate training support.
  4. `low_margin_ambiguous`: abstains inside `|p - threshold| < 0.10` (`uncertainty_band="high"`).
- **SHA-256 Attestation (`orchestrator.py`):** Verifies `model_card.json` `artifact_sha256` before loading `model.joblib`.

---

## 2. AI4I 2020 Controlled Benchmark (Strictly Separate From MetroPT-3)

- **Dataset (`factorymind.data.ai4i2020`):** UCI dataset `#601` (`CC BY 4.0`, `10,000` rows, `archive_sha256=f601f142...`).
- **Leakage Controls (`ai4i_benchmark.py`):**
  - Input features use **only** `Type`, `Air temperature [K]`, `Process temperature [K]`, `Rotational speed [rpm]`, `Torque [Nm]`, and `Tool wear [min]`.
  - Strictly excludes `UDI`, `Product ID`, `Machine failure`, `TWF`, `HDF`, `PWF`, `OSF`, and `RNF`.
  - Stratified disjoint `6,000 train / 2,000 cal / 2,000 test` split (`PlattCalibrator` and thresholds fitted strictly on `cal`; test evaluated once).

The previously reported three-model comparison later in this section retains this stratified
random split and should not be read as a time-ordered generalization estimate. UCI describes AI4I
as multivariate time-series data and notes that temperature variables follow random walks. A
separate logistic-regression baseline uses contiguous UDI-ordered splits for a temporal holdout
check.

### Chronological Logistic-Regression Baseline

`train_and_evaluate_ai4i_baseline` in `factorymind.ml.ai4i_benchmark` trains one reproducible
logistic-regression baseline with the shared feature preprocessing, Platt calibration, threshold
selection, and `compute_calibration_metrics` utility:

- The target is the per-row `Machine failure` label. Inputs are `Type` (one-hot encoded) and the
  five physical variables (standardized); the preprocessor is fitted on training rows only.
- Rows are sorted by unique integer `UDI`, then split contiguously 60% train / 20% calibration /
  20% test. `UDI` is used only as an ordering proxy and is excluded from all model features.
- `Product ID`, `Machine failure`, `TWF`, `HDF`, `PWF`, `OSF`, and `RNF` remain excluded from
  inputs. The threshold is chosen on calibration rows; final metrics are calculated once on the
  later test rows.
- The report includes ROC-AUC, PR-AUC, Brier score, ECE, precision, recall, F1, split sizes,
  failure counts, and each split's UDI range. PR-AUC and recall are especially relevant for the
  imbalanced failure target; ROC-AUC is reported as a secondary ranking metric.
- The result records the random seed and can carry the verified source archive SHA-256 when the
  caller supplies it.

### Official UCI #601 Chronological Baseline Result

The official archive was downloaded through the existing checksum-verifying adapter. With
`random_state=42`, the contiguous split used UDI ranges `1-6000` (255 failures), `6001-8000`
(45 failures), and `8001-10000` (39 failures). The threshold `0.275` was selected on calibration
rows and applied unchanged to the later test partition:

| Model | Test rows | ROC-AUC | PR-AUC | Brier | ECE (10-bin) | F1 | Precision | Recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ai4i-logreg-chronological` | 2,000 | 0.8815 | 0.4030 | 0.0156 | 0.0101 | 0.2222 | 0.8333 | 0.1282 |

This is a synthetic, same-row failure-classification baseline, not a demonstrated advance warning
or remaining-useful-life model: the CSV supplies no explicit timestamp, machine identifier, or
failure lead time, and its failure labels are generated from the physical feature values. UDI
supplies an ordering proxy but cannot establish actual elapsed time or enable machine-grouped
validation.

### Measured Held-Out Test Performance (Real UCI #601 Archive, `2,000` Test Rows)

| Model | Family | ROC-AUC | PR-AUC | Brier | ECE (10-bin) | F1 @ Threshold | Selective Acc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ai4i-logreg` | `LogisticRegression` | `0.8981` | `0.4342` | `0.0245` | `0.0082` | `0.4839` (`0.25`) | `0.9799` |
| `ai4i-rf` | `RandomForestClassifier` | `0.9763` | `0.7557` | `0.0150` | `0.0079` | `0.7368` (`0.35`) | `0.9878` |
| **`ai4i-hgb`** | **`HistGradientBoostingClassifier`** | **`0.9803`** | **`0.7984`** | **`0.0124`** | **`0.0054`** | **`0.7778` (`0.425`)** | **`0.9879`** |

- Ledoit-Wolf 99.5% Mahalanobis threshold: `5.9027`.
- **Promotion Contract:** `ai4i-hgb` passed all 4 governance gates (`leakage_free_features`, `calibration_quality`, `held_out_discrimination`, `ood_detector_ready`).

---

## 3. API, Safety & `/models` Frontend

- **API (`factorymind.api.ml_models`):**
  - `GET /api/v1/ml/catalog`
  - `GET /api/v1/ml/benchmarks/ai4i2020`
  - `POST /api/v1/ml/predict` (stateless evaluation; `require_data_source(Permission.ANOMALY_READ)` executed before loading bundles; added to explicit `test_safety_boundary.py` allowlist).
- **Frontend (`ModelsWorkspace.tsx`, `routes/models.tsx`, `__root.tsx`):**
  - Adds `/models` workspace showing domain separation disclaimer, specialist registry, AI4I calibration metrics & promotion gates, and interactive OOD/quality abstention sandbox.

