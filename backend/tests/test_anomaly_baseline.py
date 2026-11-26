from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from factorymind.anomaly.baseline import train_and_evaluate_pca_spe_baseline
from factorymind.data.metropt3 import FailureReport
from tests.conftest import simulate_apu


def _periods(*, missing_sensor: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = simulate_apu("2020-01-01", 6 * 60 * 24 * 3, seed=1)
    calibration = simulate_apu("2020-01-04", 6 * 60 * 24, seed=2)
    evaluation = simulate_apu(
        "2020-01-05",
        6 * 60 * 24,
        leak_from="2020-01-05 06:00",
        leak_to="2020-01-05 10:00",
        seed=3,
    )
    if missing_sensor:
        evaluation.loc["2020-01-05 12:00":"2020-01-05 12:09:50", "TP2"] = np.nan
    return train, calibration, evaluation


def test_pca_spe_baseline_is_reproducible_and_reports_distinct_metrics() -> None:
    train, calibration, evaluation = _periods()
    report = FailureReport(
        "SIM-1",
        datetime(2020, 1, 5, 6),
        datetime(2020, 1, 5, 10),
        "Simulated air leak",
    )
    evaluation["Machine failure"] = (
        (evaluation.index >= pd.Timestamp(report.start))
        & (evaluation.index < pd.Timestamp(report.end))
    ).astype("int64")
    original = evaluation.copy(deep=True)

    model, result = train_and_evaluate_pca_spe_baseline(
        train, calibration, evaluation, reports=(report,), seed=4
    )
    _, repeated = train_and_evaluate_pca_spe_baseline(
        train, calibration, evaluation, reports=(report,), seed=4
    )

    assert result.detector_name == "pca_spe"
    assert "not a failure probability" in result.score_meaning
    assert result.alpha == 0.01
    assert result.threshold > 0
    assert result.training_reference_windows > 5
    assert result.calibration_reference_windows > 0
    assert result.evaluation_scored_windows == result.evaluation_windows
    assert result.calibration_score_quantiles["p50"] <= result.calibration_score_quantiles["p99"]
    assert set(result.calibration_load_duty_summary) == {
        "0.0-0.1",
        "0.1-0.3",
        "0.3-0.6",
        "0.6-0.9",
    }
    assert result.evaluation.false_alarms_per_day >= 0
    assert result.evaluation.events_total == 1
    assert result.evaluation.events[0].delay_minutes is None or np.isfinite(
        result.evaluation.events[0].delay_minutes
    )
    assert result.evaluation == repeated.evaluation

    scored = model.score_telemetry(evaluation)
    assert np.isfinite(scored.loc[scored["score_available"], "anomaly_score"]).all()
    assert "failure_probability" not in scored.columns
    assert "Machine failure" not in scored.columns
    pd.testing.assert_frame_equal(evaluation, original)


def test_baseline_leaves_windows_with_missing_raw_sensors_unscored() -> None:
    train, calibration, evaluation = _periods(missing_sensor=True)
    original = evaluation.copy(deep=True)
    model, result = train_and_evaluate_pca_spe_baseline(train, calibration, evaluation, reports=())

    scores = model.score_telemetry(evaluation)
    missing_windows = scores["missing_sensor_samples"] > 0
    assert result.evaluation_missing_sensor_windows == 1
    assert missing_windows.sum() == 1
    assert not scores.loc[missing_windows, "score_available"].any()
    assert scores.loc[missing_windows, "anomaly_score"].isna().all()
    assert result.evaluation_scored_windows == result.evaluation_windows - 1
    pd.testing.assert_frame_equal(evaluation, original)


def test_baseline_rejects_non_chronological_partitions_and_report_leakage() -> None:
    train, calibration, evaluation = _periods()
    with pytest.raises(ValueError, match="chronologically separate"):
        train_and_evaluate_pca_spe_baseline(calibration, train, evaluation, reports=())

    overlapping_report = FailureReport(
        "TRAIN",
        datetime(2020, 1, 2, 6),
        datetime(2020, 1, 2, 8),
        "Air leak",
    )
    with pytest.raises(ValueError, match="overlap training"):
        train_and_evaluate_pca_spe_baseline(
            train, calibration, evaluation, reports=(overlapping_report,)
        )
