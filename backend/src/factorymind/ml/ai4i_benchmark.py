"""Controlled multi-model training, calibration, OOD fitting, and evaluation on AI4I 2020.

Leakage guarantees:
1. Feature matrix uses ONLY ``ai4i2020.FEATURE_COLUMNS`` (excludes UDI, Product ID, Machine
   failure, TWF, HDF, PWF, OSF, RNF).
2. Dataset is stratified into disjoint 60% train / 20% cal / 20% test splits before any fitting.
3. Preprocessors, classifiers, secondary mode heads, and Mahalanobis OOD detector are fit
   strictly on the train split; probability calibration and decision thresholds are fit
   strictly on the cal split; test metrics are computed once on untouched held-out rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from factorymind.contracts.ml_engine import (
    ModelCalibrationMetrics,
    ModelComparisonRow,
    ModelPromotionContract,
    ModelPromotionGate,
    MultiModelBenchmarkReport,
)
from factorymind.data import ai4i2020
from factorymind.data.metropt3 import DataValidationError
from factorymind.ml.uncertainty import MahalanobisOodDetector, compute_calibration_metrics


@dataclass(frozen=True)
class PlattCalibrator:
    """1D logistic calibration over clipped log-odds fitted strictly on the cal split."""

    slope: float
    intercept: float

    @classmethod
    def fit(cls, raw_probs: np.ndarray, y_true: np.ndarray) -> PlattCalibrator:
        clipped = np.clip(np.asarray(raw_probs, dtype="float64"), 1e-6, 1.0 - 1e-6)
        logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        lr.fit(logits, np.asarray(y_true, dtype="int64"))
        return cls(slope=float(lr.coef_[0, 0]), intercept=float(lr.intercept_[0]))

    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        clipped = np.clip(np.asarray(raw_probs, dtype="float64"), 1e-6, 1.0 - 1e-6)
        logits = np.log(clipped / (1.0 - clipped))
        z = np.clip(self.slope * logits + self.intercept, -30.0, 30.0)
        return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype="float64")


@dataclass(frozen=True)
class CalibratedCandidate:
    """One trained AI4I 2020 candidate classifier with calibration and threshold."""

    model_id: str
    model_family: str
    estimator: Any
    calibrator: PlattCalibrator | None
    threshold: float

    def predict_proba(self, transformed_x: np.ndarray) -> np.ndarray:
        raw = np.asarray(self.estimator.predict_proba(transformed_x)[:, 1], dtype="float64")
        if self.calibrator is None:
            return raw
        return self.calibrator.transform(raw)


@dataclass(frozen=True)
class Ai4iModelBundle:
    """Versioned multi-model package stored safely with SHA-256 attestation."""

    model_version: str
    asset_id: str
    dataset_id: str
    feature_columns: tuple[str, ...]
    numeric_features: tuple[str, ...]
    preprocessor: ColumnTransformer
    numeric_scaler: StandardScaler
    ood_detector: MahalanobisOodDetector
    selected_candidate: CalibratedCandidate
    candidates: dict[str, CalibratedCandidate]
    mode_heads: dict[str, Any]


@dataclass(frozen=True)
class Ai4iBaselineReport:
    """Time-ordered evaluation summary for the single-model AI4I baseline."""

    model_id: str
    dataset_id: str
    archive_sha256: str | None
    random_state: int
    target_column: str
    validation_strategy: str
    feature_columns: tuple[str, ...]
    excluded_columns: tuple[str, ...]
    split_counts: dict[str, int]
    split_positive_counts: dict[str, int]
    split_udi_ranges: dict[str, tuple[int, int]]
    calibration_metrics: ModelCalibrationMetrics
    test_metrics: ModelCalibrationMetrics


@dataclass(frozen=True)
class Ai4iBaselineModel:
    """Fitted logistic-regression baseline and its training-only preprocessing."""

    preprocessor: ColumnTransformer
    candidate: CalibratedCandidate
    feature_columns: tuple[str, ...]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        missing = [column for column in self.feature_columns if column not in frame.columns]
        if missing:
            raise DataValidationError(f"Missing baseline feature columns: {missing}")
        transformed = np.asarray(
            self.preprocessor.transform(frame.loc[:, list(self.feature_columns)]),
            dtype="float64",
        )
        return self.candidate.predict_proba(transformed)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(frame) >= self.candidate.threshold).astype("int64")


def _pick_threshold(y_cal: np.ndarray, p_cal: np.ndarray) -> float:
    best_thr, best_f1 = 0.5, -1.0
    for thr in np.linspace(0.15, 0.85, 29):
        preds = (p_cal >= thr).astype("int64")
        score = float(f1_score(y_cal, preds, zero_division=0))
        if score > best_f1:
            best_f1 = score
            best_thr = float(thr)
    return round(best_thr, 4)


def _build_ai4i_preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), list(ai4i2020.NUMERIC_FEATURES)),
            (
                "cat",
                OneHotEncoder(
                    categories=[list(ai4i2020.PRODUCT_TYPES)],
                    handle_unknown="ignore",
                    sparse_output=False,
                ),
                ["Type"],
            ),
        ],
        remainder="drop",
    )


def train_and_evaluate_ai4i_baseline(
    frame: pd.DataFrame,
    *,
    random_state: int = 42,
    archive_sha256: str | None = None,
) -> tuple[Ai4iBaselineModel, Ai4iBaselineReport]:
    """Train a logistic baseline with contiguous UDI-ordered train/cal/test partitions.

    UCI describes AI4I as time-series data, but its CSV has no timestamp or machine identifier.
    UDI is used only as the source row-order proxy for splitting and is never a model feature.
    """
    normalized = ai4i2020.normalize(frame)
    quality = ai4i2020.assess_quality(normalized)
    ai4i2020.validate_contract(quality, expected_rows=None)
    if quality.out_of_range:
        raise DataValidationError(f"Out-of-range AI4I feature values: {quality.out_of_range}")

    udi = pd.to_numeric(normalized["UDI"], errors="coerce").to_numpy(dtype="float64")
    if (
        not np.isfinite(udi).all()
        or np.any(udi != np.floor(udi))
        or len(np.unique(udi)) != len(udi)
    ):
        raise DataValidationError("UDI must contain unique finite integer row-order identifiers")

    ordered = normalized.assign(UDI=udi.astype("int64")).sort_values("UDI", kind="stable")
    n_rows = len(ordered)
    train_end = int(n_rows * 0.60)
    calibration_end = int(n_rows * 0.80)
    if train_end == 0 or calibration_end <= train_end or calibration_end == n_rows:
        raise DataValidationError("AI4I data is too small for 60/20/20 ordered splits")

    partitions = {
        "train": ordered.iloc[:train_end],
        "calibration": ordered.iloc[train_end:calibration_end],
        "test": ordered.iloc[calibration_end:],
    }
    labels = {
        name: partition[ai4i2020.TARGET_COLUMN].to_numpy(dtype="int64")
        for name, partition in partitions.items()
    }
    for name, values in labels.items():
        if len(np.unique(values)) != 2:
            raise DataValidationError(f"The {name} split must contain both machine-failure classes")

    x_train = partitions["train"].loc[:, list(ai4i2020.FEATURE_COLUMNS)]
    x_cal = partitions["calibration"].loc[:, list(ai4i2020.FEATURE_COLUMNS)]
    x_test = partitions["test"].loc[:, list(ai4i2020.FEATURE_COLUMNS)]
    y_train = labels["train"]
    y_cal = labels["calibration"]
    y_test = labels["test"]

    preprocessor = _build_ai4i_preprocessor()
    x_train_encoded = np.asarray(preprocessor.fit_transform(x_train), dtype="float64")
    x_cal_encoded = np.asarray(preprocessor.transform(x_cal), dtype="float64")
    x_test_encoded = np.asarray(preprocessor.transform(x_test), dtype="float64")

    estimator = LogisticRegression(max_iter=1000, random_state=random_state)
    estimator.fit(x_train_encoded, y_train)
    raw_cal_probs = np.asarray(estimator.predict_proba(x_cal_encoded)[:, 1], dtype="float64")
    raw_cal_metrics = compute_calibration_metrics(y_cal, raw_cal_probs, threshold=0.5)
    platt = PlattCalibrator.fit(raw_cal_probs, y_cal)
    platt_cal_probs = platt.transform(raw_cal_probs)
    use_platt = (
        compute_calibration_metrics(y_cal, platt_cal_probs, threshold=0.5).brier_score
        < raw_cal_metrics.brier_score
    )
    calibrator = platt if use_platt else None
    cal_probs = platt_cal_probs if use_platt else raw_cal_probs
    threshold = _pick_threshold(y_cal, cal_probs)
    candidate = CalibratedCandidate(
        model_id="ai4i-logreg-chronological",
        model_family="LogisticRegression",
        estimator=estimator,
        calibrator=calibrator,
        threshold=threshold,
    )
    cal_metrics = compute_calibration_metrics(y_cal, cal_probs, threshold=threshold)
    test_probs = candidate.predict_proba(x_test_encoded)
    test_metrics = compute_calibration_metrics(y_test, test_probs, threshold=threshold)

    report = Ai4iBaselineReport(
        model_id=candidate.model_id,
        dataset_id=ai4i2020.DATASET_ID,
        archive_sha256=archive_sha256,
        random_state=random_state,
        target_column=ai4i2020.TARGET_COLUMN,
        validation_strategy=(
            "Contiguous 60/20/20 train/calibration/test splits ordered by UDI; "
            "UDI is a row-order proxy only, not a feature. No timestamp or machine ID is supplied."
        ),
        feature_columns=ai4i2020.FEATURE_COLUMNS,
        excluded_columns=ai4i2020.EXCLUDED_COLUMNS,
        split_counts={name: len(partition) for name, partition in partitions.items()},
        split_positive_counts={name: int(values.sum()) for name, values in labels.items()},
        split_udi_ranges={
            name: (
                int(partition["UDI"].iloc[0]),
                int(partition["UDI"].iloc[-1]),
            )
            for name, partition in partitions.items()
        },
        calibration_metrics=cal_metrics,
        test_metrics=test_metrics,
    )
    return (
        Ai4iBaselineModel(
            preprocessor=preprocessor,
            candidate=candidate,
            feature_columns=ai4i2020.FEATURE_COLUMNS,
        ),
        report,
    )


def train_and_evaluate_ai4i(
    frame: pd.DataFrame,
    *,
    random_state: int = 42,
    archive_sha256: str = ai4i2020.ARCHIVE_SHA256,
) -> tuple[Ai4iModelBundle, MultiModelBenchmarkReport]:
    """Train, calibrate, and evaluate three AI4I 2020 classifiers without data leakage."""
    leak_overlap = sorted(set(ai4i2020.FEATURE_COLUMNS) & set(ai4i2020.EXCLUDED_COLUMNS))
    if leak_overlap:
        raise ValueError(f"Feature columns overlap forbidden target/ID columns: {leak_overlap}")

    x_all = frame.loc[:, list(ai4i2020.FEATURE_COLUMNS)].copy()
    y_all = frame[ai4i2020.TARGET_COLUMN].to_numpy(dtype="int64")

    x_train, x_temp, y_train, y_temp, idx_train, _ = train_test_split(
        x_all,
        y_all,
        np.arange(len(frame)),
        test_size=0.40,
        stratify=y_all,
        random_state=random_state,
    )
    x_cal, x_test, y_cal, y_test = train_test_split(
        x_temp,
        y_temp,
        test_size=0.50,
        stratify=y_temp,
        random_state=random_state,
    )

    preprocessor = _build_ai4i_preprocessor()
    x_tr_enc = np.asarray(preprocessor.fit_transform(x_train), dtype="float64")
    x_cal_enc = np.asarray(preprocessor.transform(x_cal), dtype="float64")
    x_te_enc = np.asarray(preprocessor.transform(x_test), dtype="float64")

    num_scaler = StandardScaler().fit(x_train.loc[:, list(ai4i2020.NUMERIC_FEATURES)].to_numpy())
    x_tr_num = np.asarray(
        num_scaler.transform(x_train.loc[:, list(ai4i2020.NUMERIC_FEATURES)].to_numpy()),
        dtype="float64",
    )
    ood = MahalanobisOodDetector.fit(x_tr_num, quantile=0.995)

    specs: list[tuple[str, str, Any]] = [
        (
            "ai4i-logreg",
            "LogisticRegression",
            LogisticRegression(max_iter=1000, random_state=random_state),
        ),
        (
            "ai4i-rf",
            "RandomForestClassifier",
            RandomForestClassifier(
                n_estimators=180,
                min_samples_leaf=3,
                random_state=random_state,
                n_jobs=1,
            ),
        ),
        (
            "ai4i-hgb",
            "HistGradientBoostingClassifier",
            HistGradientBoostingClassifier(
                max_depth=5,
                learning_rate=0.08,
                max_iter=150,
                random_state=random_state,
            ),
        ),
    ]

    candidates: dict[str, CalibratedCandidate] = {}
    rows: list[ModelComparisonRow] = []

    for model_id, family, estimator in specs:
        estimator.fit(x_tr_enc, y_train)
        raw_cal = np.asarray(estimator.predict_proba(x_cal_enc)[:, 1], dtype="float64")
        raw_cal_metrics = compute_calibration_metrics(y_cal, raw_cal, threshold=0.5)

        platt = PlattCalibrator.fit(raw_cal, y_cal)
        platt_cal = platt.transform(raw_cal)
        platt_cal_metrics = compute_calibration_metrics(y_cal, platt_cal, threshold=0.5)

        use_platt = platt_cal_metrics.brier_score < raw_cal_metrics.brier_score
        calibrator = platt if use_platt else None
        final_cal_probs = platt_cal if use_platt else raw_cal
        threshold = _pick_threshold(y_cal, final_cal_probs)

        cal_metrics = compute_calibration_metrics(y_cal, final_cal_probs, threshold=threshold)
        cand = CalibratedCandidate(
            model_id=model_id,
            model_family=family,
            estimator=estimator,
            calibrator=calibrator,
            threshold=threshold,
        )
        test_probs = cand.predict_proba(x_te_enc)
        test_metrics = compute_calibration_metrics(y_test, test_probs, threshold=threshold)

        candidates[model_id] = cand
        rows.append(
            ModelComparisonRow(
                model_id=model_id,
                model_family=family,
                dataset_id=ai4i2020.DATASET_ID,
                cal_metrics=cal_metrics,
                test_metrics=test_metrics,
                ood_threshold=round(ood.threshold, 4),
            )
        )

    best_row = min(rows, key=lambda r: (r.cal_metrics.brier_score, -r.cal_metrics.roc_auc))
    selected = candidates[best_row.model_id]

    mode_heads: dict[str, Any] = {}
    train_subset = frame.iloc[idx_train]
    for mode in ai4i2020.MODE_COLUMNS:
        ym = train_subset[mode].to_numpy(dtype="int64")
        clf = HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.08, max_iter=100, random_state=random_state
        )
        clf.fit(x_tr_enc, ym)
        mode_heads[mode] = clf

    gates = [
        ModelPromotionGate(
            gate_id="leakage_free_features",
            passed=len(leak_overlap) == 0,
            detail=(
                "Only physical inputs (Type + 5 sensors) used; IDs and failure targets excluded."
            ),
        ),
        ModelPromotionGate(
            gate_id="calibration_quality",
            passed=best_row.test_metrics.brier_score <= 0.05
            and best_row.test_metrics.ece_10_bins <= 0.05,
            detail=(
                f"Held-out Brier={best_row.test_metrics.brier_score:.4f} (<=0.05), "
                f"ECE={best_row.test_metrics.ece_10_bins:.4f} (<=0.05)."
            ),
        ),
        ModelPromotionGate(
            gate_id="held_out_discrimination",
            passed=best_row.test_metrics.roc_auc >= 0.90 and best_row.test_metrics.pr_auc >= 0.65,
            detail=(
                f"Held-out ROC-AUC={best_row.test_metrics.roc_auc:.4f} (>=0.90), "
                f"PR-AUC={best_row.test_metrics.pr_auc:.4f} (>=0.65)."
            ),
        ),
        ModelPromotionGate(
            gate_id="ood_detector_ready",
            passed=ood.threshold > 0.0 and np.isfinite(ood.threshold),
            detail=f"Ledoit-Wolf Mahalanobis 99.5% OOD threshold={ood.threshold:.4f}.",
        ),
    ]
    promoted = all(g.passed for g in gates)
    promotion = ModelPromotionContract(
        asset_id="ai4i-milling-bench",
        dataset_id=ai4i2020.DATASET_ID,
        candidate_model_id=best_row.model_id,
        promoted=promoted,
        gates=gates,
        summary=(
            f"Candidate {best_row.model_id} passed all {len(gates)} calibration/OOD promotion "
            f"gates for AI4I 2020 controlled benchmark."
            if promoted
            else f"Candidate {best_row.model_id} failed one or more promotion gates."
        ),
    )

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_version = f"ai4i2020-{ts}-{best_row.model_id}"
    bundle = Ai4iModelBundle(
        model_version=model_version,
        asset_id="ai4i-milling-bench",
        dataset_id=ai4i2020.DATASET_ID,
        feature_columns=ai4i2020.FEATURE_COLUMNS,
        numeric_features=ai4i2020.NUMERIC_FEATURES,
        preprocessor=preprocessor,
        numeric_scaler=num_scaler,
        ood_detector=ood,
        selected_candidate=selected,
        candidates=candidates,
        mode_heads=mode_heads,
    )

    report = MultiModelBenchmarkReport(
        dataset_id=ai4i2020.DATASET_ID,
        dataset_scope_note=(
            "UCI AI4I 2020 controlled synthetic milling benchmark (CC BY 4.0, 10,000 rows). "
            "Results are strictly separated from real MetroPT-3 operational compressor telemetry."
        ),
        archive_sha256=archive_sha256,
        rows_total=len(frame),
        split_counts={"train": len(x_train), "cal": len(x_cal), "test": len(x_test)},
        leakage_free_features=list(ai4i2020.FEATURE_COLUMNS),
        excluded_target_and_id_columns=list(ai4i2020.EXCLUDED_COLUMNS),
        models=rows,
        selected_model_id=best_row.model_id,
        promotion_contract=promotion,
        generated_at=datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
    )
    return bundle, report


def save_ai4i_artifacts(
    bundle: Ai4iModelBundle,
    report: MultiModelBenchmarkReport,
    artifacts_dir: Path,
) -> Path:
    """Persist SHA-256 attested AI4I bundle, model card, and benchmark report JSON."""
    bench_dir = artifacts_dir / "benchmarks" / ai4i2020.DATASET_ID
    bench_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bench_dir / "model.joblib"
    joblib.dump(bundle, bundle_path, compress=3)
    bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

    (bench_dir / "benchmark_report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    card = {
        "schema_version": 1,
        "model_version": bundle.model_version,
        "dataset_id": bundle.dataset_id,
        "asset_id": bundle.asset_id,
        "selected_model_id": report.selected_model_id,
        "artifact_sha256": bundle_sha256,
        "promoted": report.promotion_contract.promoted,
    }
    (bench_dir / "model_card.json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return bench_dir


def run_ai4i_benchmark(
    raw_dir: Path,
    processed_dir: Path,
    artifacts_dir: Path,
    *,
    random_state: int = 42,
) -> MultiModelBenchmarkReport:
    """Prepare real AI4I 2020 archive, execute multi-model benchmark, and save artifacts."""
    ai4i2020.prepare(raw_dir, processed_dir)
    frame = ai4i2020.load_processed(processed_dir)
    bundle, report = train_and_evaluate_ai4i(frame, random_state=random_state)
    save_ai4i_artifacts(bundle, report, artifacts_dir)
    return report
