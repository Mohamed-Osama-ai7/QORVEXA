import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from factorymind.data import metropt3
from factorymind.data.metropt3 import (
    DataValidationError,
    assess_quality,
    clean,
    normalise,
    validate_contract,
)
from tests.conftest import simulate_apu


def _raw_csv(frame: pd.DataFrame) -> pd.DataFrame:
    raw = frame.reset_index()
    raw["timestamp"] = raw["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    raw.insert(0, "Unnamed: 0", np.arange(len(raw)) * 10)
    return raw


def test_normalise_applies_contract_and_drops_source_index() -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 20))
    raw["Machine failure"] = np.arange(len(raw)) % 2
    original = raw.copy(deep=True)
    out = normalise(raw)
    assert isinstance(out.index, pd.DatetimeIndex)
    assert list(out.columns) == list(metropt3.SENSORS)
    assert (out.dtypes == "float64").all()
    assert "Machine failure" not in out.columns
    pd.testing.assert_frame_equal(raw, original)


def test_normalise_rejects_missing_columns() -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 5)).drop(columns=["TP3"])
    with pytest.raises(DataValidationError, match="TP3"):
        normalise(raw)


def test_normalise_rejects_bad_timestamps() -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 5))
    raw.loc[2, "timestamp"] = "not-a-time"
    with pytest.raises(DataValidationError, match="timestamps"):
        normalise(raw)


def test_normalise_rejects_timezone_aware_timestamps_and_non_numeric_sensors() -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 5))
    raw["timestamp"] = pd.date_range("2020-02-01", periods=5, freq="10s", tz="UTC")
    with pytest.raises(DataValidationError, match="Timezone-aware"):
        normalise(raw)

    raw = _raw_csv(simulate_apu("2020-02-01", 5))
    raw["TP3"] = raw["TP3"].astype("object")
    raw.loc[2, "TP3"] = "not-a-pressure"
    with pytest.raises(DataValidationError, match=r"TP3.*non-numeric"):
        normalise(raw)


def test_normalise_rejects_duplicate_column_names() -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 5))
    raw = pd.concat([raw, raw[["TP2"]]], axis=1)
    with pytest.raises(DataValidationError, match="Duplicate column names"):
        normalise(raw)


def test_quality_report_and_cleaning_handle_corruption() -> None:
    frame = simulate_apu("2020-02-01", 100)
    frame = pd.concat([frame, frame.iloc[[10]]])  # duplicate timestamp
    frame.iloc[5, frame.columns.get_loc("TP3")] = 99.0  # out of physical range
    frame.iloc[6, frame.columns.get_loc("LPS")] = 0.5  # non-binary digital
    frame.iloc[7, frame.columns.get_loc("H1")] = np.nan
    frame["Machine failure"] = np.nan
    original = frame.copy(deep=True)
    report = assess_quality(frame)
    assert report.duplicate_timestamps == 1
    assert report.out_of_order_timestamps == 1
    assert report.out_of_range == {"TP3": 1}
    assert report.non_binary_digital == {"LPS": 1}
    assert report.null_values == 1
    cleaned = clean(frame)
    assert cleaned.index.is_unique and cleaned.index.is_monotonic_increasing
    assert "Machine failure" not in cleaned.columns
    assert len(cleaned) == 100 - 3  # duplicate removed, three corrupt rows dropped
    assert not cleaned.isna().any().any()
    pd.testing.assert_frame_equal(frame, original)


def test_quality_report_detects_gaps() -> None:
    a = simulate_apu("2020-02-01 00:00", 30)
    b = simulate_apu("2020-02-01 02:00", 30)
    report = assess_quality(pd.concat([a, b]))
    assert report.gaps_over_60s == 1
    assert report.median_period_s == 10.0


def test_quality_report_counts_out_of_order_timestamps_and_contract_rejects_them() -> None:
    frame = simulate_apu("2020-02-01", 20)
    frame = pd.concat([frame.iloc[:5], frame.iloc[[6, 5]], frame.iloc[7:]])
    report = assess_quality(frame)
    assert report.out_of_order_timestamps == 1
    assert report.median_period_s == 10.0
    with pytest.raises(DataValidationError, match="out-of-order"):
        validate_contract(report, expected_rows=None)


def test_validate_contract_flags_row_count_and_duplicates() -> None:
    frame = simulate_apu("2020-02-01", 50)
    report = assess_quality(pd.concat([frame, frame.iloc[[0]]]))
    with pytest.raises(DataValidationError, match="duplicate"):
        validate_contract(report, expected_rows=None)
    with pytest.raises(DataValidationError, match="row count"):
        validate_contract(assess_quality(frame))


def test_read_raw_from_archive(tmp_path: Path) -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 30))
    archive = tmp_path / "metropt3.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        buf = io.StringIO()
        raw.to_csv(buf, index=False)
        zf.writestr(metropt3.CSV_MEMBER, buf.getvalue())
    out = metropt3.read_raw(archive)
    assert len(out) == 30


def test_prepare_records_cleaning_and_preserves_input_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _raw_csv(simulate_apu("2020-02-01", 20))
    raw.loc[3, "TP3"] = np.nan
    original = raw.copy(deep=True)
    monkeypatch.setattr(metropt3, "EXPECTED_ROWS", len(raw))
    monkeypatch.setattr(metropt3, "download_archive", lambda destination: destination)
    monkeypatch.setattr(metropt3, "read_raw", lambda archive: normalise(raw))
    monkeypatch.setattr(
        metropt3,
        "validate_contract",
        lambda report: validate_contract(report, expected_rows=None),
    )

    manifest = metropt3.prepare(tmp_path / "raw", tmp_path / "processed")

    assert manifest["raw_quality"]["null_values"] == 1
    assert manifest["cleaning"]["rows_before"] == 20
    assert manifest["cleaning"]["rows_removed"] == 1
    assert manifest["cleaning"]["gap_policy"] == "preserve_without_interpolation"
    assert len(metropt3.load_processed(tmp_path / "processed")) == 19
    pd.testing.assert_frame_equal(raw, original)


def test_download_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "metropt3.zip"
    archive.write_bytes(b"not the real archive")
    with pytest.raises(DataValidationError, match="checksum"):
        metropt3.download_archive(archive)


def test_failure_reports_are_chronological_and_well_formed() -> None:
    reports = metropt3.FAILURE_REPORTS
    assert len(reports) == 4
    assert all(r.start < r.end for r in reports)
    assert [r.start for r in reports] == sorted(r.start for r in reports)
