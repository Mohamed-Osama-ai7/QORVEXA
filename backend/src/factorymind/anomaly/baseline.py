"""Reproducible, time-ordered PCA-SPE baseline for MetroPT-3 telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd

from factorymind.anomaly.alarm import AlarmPolicy
from factorymind.anomaly.calibration import ExceedanceCalibrator
from factorymind.anomaly.detectors import PcaSpe
from factorymind.anomaly.evaluation import EvaluationResult, evaluate
from factorymind.anomaly.features import FEATURE_NAMES, WindowSpec, window_features
from factorymind.data import metropt3

_LOAD_DUTY_BANDS: Final[tuple[tuple[str, float, float], ...]] = (
    ("0.0-0.1", 0.0, 0.1),
    ("0.1-0.3", 0.1, 0.3),
    ("0.3-0.6", 0.3, 0.6),
    ("0.6-0.9", 0.6, 0.9),
)


@dataclass(frozen=True)
class PcaSpeBaselineReport:
    """Evaluation and operating-reference summaries; scores are not failure probabilities."""

    detector_name: str
    score_meaning: str
    alpha: float
    threshold: float
    feature_names: tuple[str, ...]
    training_reference_windows: int
    calibration_reference_windows: int
    evaluation_windows: int
    evaluation_scored_windows: int
    evaluation_missing_sensor_windows: int
    calibration_score_quantiles: dict[str, float]
    calibration_load_duty_summary: dict[str, dict[str, float | int | None]]
    evaluation: EvaluationResult


@dataclass(frozen=True)
class PcaSpeBaselineModel:
    detector: PcaSpe
    calibrator: ExceedanceCalibrator
    threshold: float
    feature_names: tuple[str, ...]
    spec: WindowSpec

    def score_telemetry(self, telemetry: pd.DataFrame) -> pd.DataFrame:
        """Score valid windows; windows with any missing raw sensor sample remain unscored."""
        windows, missing_counts = _build_windows(telemetry, self.spec)
        eligible = windows["valid"].astype(bool) & missing_counts.eq(0)
        scores = np.full(len(windows), np.nan, dtype="float64")
        if eligible.any():
            scores[eligible.to_numpy()] = self.detector.score(
                windows.loc[eligible, list(self.feature_names)]
            )
        out = windows.drop(columns=list(FEATURE_NAMES), errors="ignore").copy()
        out["missing_sensor_samples"] = missing_counts
        out["score_available"] = eligible.to_numpy()
        out["anomaly_score"] = scores
        return out


def _validate_telemetry(telemetry: pd.DataFrame) -> None:
    if telemetry.empty:
        raise ValueError("Telemetry partition must not be empty")
    if not isinstance(telemetry.index, pd.DatetimeIndex):
        raise ValueError("Telemetry must use a DatetimeIndex")
    index = pd.DatetimeIndex(telemetry.index)
    if index.tz is not None:
        raise ValueError("MetroPT-3 timestamps must be timezone-naive")
    if index.hasnans or not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("Telemetry timestamps must be non-missing, unique, and increasing")
    missing_columns = [column for column in metropt3.SENSORS if column not in telemetry.columns]
    if missing_columns:
        raise ValueError(f"Telemetry is missing required sensor columns: {missing_columns}")
    non_numeric = [
        column
        for column in metropt3.SENSORS
        if not pd.api.types.is_numeric_dtype(telemetry[column].dtype)
    ]
    if non_numeric:
        raise ValueError(f"Sensor columns must be numeric: {non_numeric}")

    quality = metropt3.assess_quality(telemetry)
    if quality.out_of_range:
        raise ValueError(f"Telemetry contains out-of-range values: {quality.out_of_range}")
    if quality.non_binary_digital:
        raise ValueError(
            f"Telemetry contains non-binary digital values: {quality.non_binary_digital}"
        )


def _build_windows(telemetry: pd.DataFrame, spec: WindowSpec) -> tuple[pd.DataFrame, pd.Series]:
    _validate_telemetry(telemetry)
    windows = window_features(telemetry, spec)
    bucket = pd.DatetimeIndex(telemetry.index).floor(spec.length)
    missing_rows = telemetry.loc[:, list(metropt3.SENSORS)].isna().any(axis=1)
    missing_counts = missing_rows.groupby(bucket).sum().reindex(windows.index, fill_value=0)
    missing_counts.name = "missing_sensor_samples"
    return windows, missing_counts.astype("int64")


def _reference_mask(windows: pd.DataFrame, missing_counts: pd.Series) -> pd.Series:
    return (
        windows["valid"].astype(bool)
        & ~windows["stopped"].astype(bool)
        & (windows["load_duty"] <= 0.9)
        & missing_counts.eq(0)
    )


def _validate_periods(
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    evaluation: pd.DataFrame,
    spec: WindowSpec,
    reports: tuple[metropt3.FailureReport, ...],
) -> None:
    frames = (train, calibration, evaluation)
    for frame in frames:
        _validate_telemetry(frame)
    boundaries = [pd.DatetimeIndex(frame.index).floor(spec.length)[[0, -1]] for frame in frames]
    if not (boundaries[0][1] < boundaries[1][0] < boundaries[1][1] < boundaries[2][0]):
        raise ValueError(
            "Train, calibration, and evaluation windows must be chronologically separate"
        )

    for name, frame in (("training", train), ("calibration", calibration)):
        start, end = pd.Timestamp(frame.index[0]), pd.Timestamp(frame.index[-1])
        overlaps = [
            report.report_id
            for report in reports
            if start <= pd.Timestamp(report.end) and end >= pd.Timestamp(report.start)
        ]
        if overlaps:
            raise ValueError(f"Weak failure reports overlap {name} data: {overlaps}")


def _load_duty_summary(
    windows: pd.DataFrame,
    mask: pd.Series,
    scores: np.ndarray,
    threshold: float,
) -> dict[str, dict[str, float | int | None]]:
    summary: dict[str, dict[str, float | int | None]] = {}
    duties = windows.loc[mask, "load_duty"].to_numpy(dtype="float64")
    for name, lower, upper in _LOAD_DUTY_BANDS:
        in_band = (duties >= lower) & (duties < upper)
        values = scores[in_band]
        summary[name] = {
            "windows": len(values),
            "median_score": float(np.median(values)) if len(values) else None,
            "p95_score": float(np.quantile(values, 0.95)) if len(values) else None,
            "threshold_exceedance_rate": (
                float(np.mean(values > threshold)) if len(values) else None
            ),
        }
    return summary


def train_and_evaluate_pca_spe_baseline(
    train_telemetry: pd.DataFrame,
    calibration_telemetry: pd.DataFrame,
    evaluation_telemetry: pd.DataFrame,
    *,
    reports: tuple[metropt3.FailureReport, ...] = metropt3.FAILURE_REPORTS,
    spec: WindowSpec | None = None,
    alpha: float = 0.01,
    persistence: int = 3,
    seed: int = 42,
) -> tuple[PcaSpeBaselineModel, PcaSpeBaselineReport]:
    """Fit PCA-SPE on normal train windows and calibrate a threshold on later normal windows."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    if persistence < 1:
        raise ValueError("persistence must be >= 1")
    spec = spec or WindowSpec()
    _validate_periods(train_telemetry, calibration_telemetry, evaluation_telemetry, spec, reports)

    train_windows, train_missing = _build_windows(train_telemetry, spec)
    calibration_windows, calibration_missing = _build_windows(calibration_telemetry, spec)
    evaluation_windows, evaluation_missing = _build_windows(evaluation_telemetry, spec)
    train_mask = _reference_mask(train_windows, train_missing)
    calibration_mask = _reference_mask(calibration_windows, calibration_missing)
    if int(train_mask.sum()) < 5:
        raise ValueError("At least five normal, complete training windows are required")
    if not calibration_mask.any():
        raise ValueError("No normal, complete calibration windows are available")

    train_features = train_windows.loc[train_mask, list(FEATURE_NAMES)]
    feature_names = tuple(
        column for column in FEATURE_NAMES if train_features[column].notna().any()
    )
    if len(feature_names) < 2:
        raise ValueError("At least two non-empty window features are required for PCA-SPE")

    detector = PcaSpe(variance=0.95, seed=seed).fit(train_features.loc[:, list(feature_names)])
    calibration_scores = detector.score(
        calibration_windows.loc[calibration_mask, list(feature_names)]
    )
    if not np.isfinite(calibration_scores).all():
        raise ValueError("Calibration produced non-finite anomaly scores")
    calibrator = ExceedanceCalibrator.fit(calibration_scores)
    threshold = calibrator.threshold_for(alpha)
    model = PcaSpeBaselineModel(detector, calibrator, threshold, feature_names, spec)

    evaluation_scores = model.score_telemetry(evaluation_telemetry)
    available = evaluation_scores["score_available"].astype(bool)
    scored = evaluation_scores.loc[available]
    if scored.empty:
        raise ValueError("No valid, complete evaluation windows are available to score")
    eval_start = pd.Timestamp(evaluation_telemetry.index[0])
    eval_end = pd.Timestamp(evaluation_telemetry.index[-1])
    evaluation_reports = tuple(
        report
        for report in reports
        if pd.Timestamp(report.end) >= eval_start and pd.Timestamp(report.start) <= eval_end
    )
    metrics = evaluate(
        pd.DatetimeIndex(scored.index),
        scored["anomaly_score"].to_numpy(dtype="float64"),
        pd.Timedelta(spec.length),
        AlarmPolicy(threshold=threshold, persistence=persistence),
        evaluation_reports,
        early_horizon=pd.Timedelta(hours=6),
        post_tolerance=pd.Timedelta(hours=24),
        suppress=scored["stopped"].to_numpy(dtype=bool),
    )
    calibration_values = {
        "p50": float(np.quantile(calibration_scores, 0.50)),
        "p90": float(np.quantile(calibration_scores, 0.90)),
        "p95": float(np.quantile(calibration_scores, 0.95)),
        "p99": float(np.quantile(calibration_scores, 0.99)),
    }
    report = PcaSpeBaselineReport(
        detector_name=detector.name,
        score_meaning=(
            "Raw PCA squared prediction error (SPE/Q statistic); higher means less consistent "
            "with the fitted reference, not a failure probability or causal conclusion."
        ),
        alpha=alpha,
        threshold=threshold,
        feature_names=feature_names,
        training_reference_windows=int(train_mask.sum()),
        calibration_reference_windows=int(calibration_mask.sum()),
        evaluation_windows=len(evaluation_windows),
        evaluation_scored_windows=len(scored),
        evaluation_missing_sensor_windows=int(evaluation_missing.gt(0).sum()),
        calibration_score_quantiles=calibration_values,
        calibration_load_duty_summary=_load_duty_summary(
            calibration_windows, calibration_mask, calibration_scores, threshold
        ),
        evaluation=metrics,
    )
    return model, report


__all__ = [
    "PcaSpeBaselineModel",
    "PcaSpeBaselineReport",
    "train_and_evaluate_pca_spe_baseline",
]
