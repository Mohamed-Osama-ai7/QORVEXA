"""MetroPT-3 dataset adapter: schema, acquisition, validation and processed storage.

Source: UCI Machine Learning Repository, dataset 791 (DOI 10.24432/C5VW3R), CC BY 4.0.
Real telemetry from the Air Production Unit (APU) compressor of a Porto metro train,
February-August 2020. The published CSV is a 10x downsample (~0.1 Hz) of a 1 Hz log; the
source index column steps by 10, which reconciles the UCI page (1,516,948 rows, 0.1 Hz) with
the bundled description PDF (15,169,480 points, 1 Hz).

Labels: the dataset is unlabelled. The operator's failure reports (four air-leak windows) are
used as weak event labels for evaluation only. The report table on the source page has
numbering errors (two rows labelled "#1") and ambiguous maintenance-date alignment; only the
start/end times are used here.

The adapter treats timestamps as timezone-naive local train time and does not infer a timezone.
Sensor units come from the dataset documentation; numeric values are checked against generous
plausibility bounds, which cannot verify calibration or prove the unit used by an upstream source.
Normalization keeps only the timestamp and registered sensor allowlist, so identifiers and labels
cannot enter the telemetry features. The verified source archive remains unchanged.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

DATASET_ID: Final = "metropt3"
SOURCE_URL: Final = "https://archive.ics.uci.edu/static/public/791/metropt+3+dataset.zip"
SOURCE_PAGE: Final = "https://archive.ics.uci.edu/dataset/791/metropt+3+dataset"
LICENSE: Final = "CC BY 4.0"
ARCHIVE_SHA256: Final = "aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a"
CSV_MEMBER: Final = "MetroPT3(AirCompressor).csv"
EXPECTED_ROWS: Final = 1_516_948
NOMINAL_PERIOD_S: Final = 10.0
MIN_PERIOD_S: Final = 5.0
MAX_PERIOD_S: Final = 15.0

ANALOG_SENSORS: Final[dict[str, str]] = {
    "TP2": "bar",  # compressor pressure
    "TP3": "bar",  # pneumatic panel pressure
    "H1": "bar",  # cyclonic separator filter discharge pressure drop
    "DV_pressure": "bar",  # air-dryer tower discharge pressure drop (0 = under load)
    "Reservoirs": "bar",  # downstream reservoir pressure
    "Oil_temperature": "degC",
    "Motor_current": "A",  # ~0 off, ~4 offloaded, ~7 loaded, ~9 starting
}
DIGITAL_SENSORS: Final[tuple[str, ...]] = (
    "COMP",  # intake valve signal: active when no air intake (off/offloaded)
    "DV_eletric",  # outlet valve signal: active when under load (source spelling)
    "Towers",  # active drying tower (0 = tower one, 1 = tower two)
    "MPG",  # starts compressor under load when APU pressure < 8.2 bar
    "LPS",  # low-pressure switch: active when pressure < 7 bar
    "Pressure_switch",  # air-drying tower discharge detected
    "Oil_level",  # active when oil below expected level
    "Caudal_impulses",  # air-flow pulse output
)
SENSORS: Final[tuple[str, ...]] = (*ANALOG_SENSORS, *DIGITAL_SENSORS)

# Plausible physical ranges used for validation (generous; flags corrupt values, not anomalies).
VALID_RANGES: Final[dict[str, tuple[float, float]]] = {
    "TP2": (-1.0, 15.0),
    "TP3": (-1.0, 15.0),
    "H1": (-1.0, 15.0),
    "DV_pressure": (-1.0, 15.0),
    "Reservoirs": (-1.0, 15.0),
    "Oil_temperature": (-20.0, 150.0),
    "Motor_current": (-1.0, 20.0),
}


@dataclass(frozen=True)
class FailureReport:
    report_id: str
    start: datetime
    end: datetime
    failure: str


# Start/end times as published by the operator (local train time, timezone not stated).
FAILURE_REPORTS: Final[tuple[FailureReport, ...]] = (
    FailureReport("F1", datetime(2020, 4, 18, 0, 0), datetime(2020, 4, 18, 23, 59), "Air leak"),
    FailureReport("F2", datetime(2020, 5, 29, 23, 30), datetime(2020, 5, 30, 6, 0), "Air leak"),
    FailureReport("F3", datetime(2020, 6, 5, 10, 0), datetime(2020, 6, 7, 14, 30), "Air leak"),
    FailureReport("F4", datetime(2020, 7, 15, 14, 30), datetime(2020, 7, 15, 19, 0), "Air leak"),
)


class DataValidationError(ValueError):
    """Raised when telemetry violates the dataset contract."""


@dataclass(frozen=True)
class QualityReport:
    rows: int
    start: str
    end: str
    duplicate_timestamps: int
    out_of_order_timestamps: int
    null_values: int
    out_of_range: dict[str, int]
    non_binary_digital: dict[str, int]
    median_period_s: float
    gaps_over_60s: int
    gap_hours: float

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(destination: Path) -> Path:
    """Download the official archive (if absent) and verify its SHA-256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        tmp = destination.with_suffix(".part")
        with urllib.request.urlopen(SOURCE_URL, timeout=600) as response, tmp.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        tmp.replace(destination)
    actual = sha256_file(destination)
    if actual != ARCHIVE_SHA256:
        raise DataValidationError(
            f"Archive checksum mismatch for {destination}: expected {ARCHIVE_SHA256}, got {actual}"
        )
    return destination


def read_raw(archive: Path) -> pd.DataFrame:
    """Read the CSV from the archive into a typed frame indexed by timestamp."""
    with zipfile.ZipFile(archive) as zf, zf.open(CSV_MEMBER) as raw:
        frame = pd.read_csv(io.TextIOWrapper(raw, encoding="utf-8"))
    return normalise(frame)


def normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the allowlisted schema without mutating input or inferring timestamp timezones."""
    if not frame.columns.is_unique:
        raise DataValidationError("Duplicate column names are not allowed")
    missing = [c for c in ("timestamp", *SENSORS) if c not in frame.columns]
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    try:
        timestamp_index = pd.DatetimeIndex(
            pd.to_datetime(frame["timestamp"], format="%Y-%m-%d %H:%M:%S", errors="raise")
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataValidationError(f"Unparseable timestamps: {exc}") from exc
    if timestamp_index.tz is not None:
        raise DataValidationError(
            "Timezone-aware timestamps are not supported; source timezone is unspecified"
        )
    if timestamp_index.hasnans:
        raise DataValidationError("Timestamps must not be missing")

    out = frame.loc[:, list(SENSORS)].copy(deep=True)
    out.index = timestamp_index
    out.index.name = "timestamp"
    for column in SENSORS:
        try:
            out[column] = pd.to_numeric(out[column], errors="raise").astype("float64")
        except (ValueError, TypeError) as exc:
            raise DataValidationError(
                f"Sensor column {column} contains non-numeric values"
            ) from exc
    return out


def _validate_sensor_frame(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if not frame.columns.is_unique:
        raise DataValidationError("Duplicate column names are not allowed")
    missing = [column for column in SENSORS if column not in frame.columns]
    if missing:
        raise DataValidationError(f"Missing required sensor columns: {missing}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise DataValidationError("Telemetry index must be a DatetimeIndex")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        raise DataValidationError(
            "Timezone-aware timestamps are not supported; source timezone is unspecified"
        )
    if index.hasnans:
        raise DataValidationError("Timestamps must not be missing")
    non_numeric = [
        column for column in SENSORS if not pd.api.types.is_numeric_dtype(frame[column].dtype)
    ]
    if non_numeric:
        raise DataValidationError(f"Sensor columns must be numeric: {non_numeric}")
    return index


def assess_quality(frame: pd.DataFrame) -> QualityReport:
    index = _validate_sensor_frame(frame)
    source_deltas = index.to_series().diff().dt.total_seconds().to_numpy()[1:]
    ordered_unique = index.unique().sort_values()
    deltas = ordered_unique.to_series().diff().dt.total_seconds().to_numpy()[1:]
    gaps = deltas[deltas > 60]
    out_of_range = {
        col: int(((frame[col] < lo) | (frame[col] > hi)).sum())
        for col, (lo, hi) in VALID_RANGES.items()
    }
    non_binary = {
        col: int((~frame[col].isin([0.0, 1.0]) & frame[col].notna()).sum())
        for col in DIGITAL_SENSORS
    }
    return QualityReport(
        rows=len(frame),
        start=str(index.min()),
        end=str(index.max()),
        duplicate_timestamps=int(index.duplicated().sum()),
        out_of_order_timestamps=int(np.count_nonzero(source_deltas < 0)),
        null_values=int(frame.loc[:, list(SENSORS)].isna().sum().sum()),
        out_of_range={k: v for k, v in out_of_range.items() if v},
        non_binary_digital={k: v for k, v in non_binary.items() if v},
        median_period_s=float(np.median(deltas)) if len(deltas) else float("nan"),
        gaps_over_60s=len(gaps),
        gap_hours=round(float(gaps.sum()) / 3600, 2),
    )


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a cleaned copy using only registered sensors.

    Duplicate timestamps keep the first row after sorting. Out-of-range analog values and
    non-binary digital values become missing, then any incomplete row is dropped. Gaps are not
    interpolated. The input frame is never mutated.
    """
    _validate_sensor_frame(frame)
    out = frame.loc[:, list(SENSORS)].copy(deep=True).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    for col, (lo, hi) in VALID_RANGES.items():
        out.loc[(out[col] < lo) | (out[col] > hi), col] = np.nan
    for col in DIGITAL_SENSORS:
        out.loc[~out[col].isin([0.0, 1.0]), col] = np.nan
    return out.dropna(how="any")


def validate_contract(report: QualityReport, *, expected_rows: int | None = EXPECTED_ROWS) -> None:
    """Fail loudly if the source no longer matches the documented contract."""
    problems: list[str] = []
    if expected_rows is not None and report.rows != expected_rows:
        problems.append(f"row count {report.rows} != expected {expected_rows}")
    if report.duplicate_timestamps:
        problems.append(f"{report.duplicate_timestamps} duplicate timestamps")
    if report.out_of_order_timestamps:
        problems.append(f"{report.out_of_order_timestamps} out-of-order timestamps")
    if not MIN_PERIOD_S <= report.median_period_s <= MAX_PERIOD_S:
        problems.append(
            f"median sampling period {report.median_period_s}s outside "
            f"{MIN_PERIOD_S:g}-{MAX_PERIOD_S:g}s"
        )
    if problems:
        raise DataValidationError("; ".join(problems))


def prepare(raw_dir: Path, processed_dir: Path) -> dict[str, object]:
    """Download (if needed), validate, clean and store the dataset as Parquet."""
    archive = download_archive(raw_dir / DATASET_ID / "metropt3.zip")
    frame = read_raw(archive)
    raw_report = assess_quality(frame)
    validate_contract(raw_report)
    cleaned = clean(frame)
    out_dir = processed_dir / DATASET_ID
    out_dir.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(out_dir / "telemetry.parquet", engine="pyarrow")
    manifest: dict[str, object] = {
        "dataset_id": DATASET_ID,
        "source_url": SOURCE_URL,
        "source_page": SOURCE_PAGE,
        "license": LICENSE,
        "archive_sha256": ARCHIVE_SHA256,
        "raw_quality": raw_report.to_dict(),
        "rows_after_cleaning": len(cleaned),
        "cleaning": {
            "rows_before": raw_report.rows,
            "rows_removed": raw_report.rows - len(cleaned),
            "duplicate_policy": (
                "prepare rejects duplicate timestamps; clean() keeps the first after sorting"
            ),
            "invalid_measurement_policy": (
                "out-of-range analog and non-binary digital values become missing; "
                "rows with any missing measurement are dropped"
            ),
            "gap_policy": "preserve_without_interpolation",
        },
        "failure_reports": [
            {"id": f.report_id, "start": f.start.isoformat(), "end": f.end.isoformat()}
            for f in FAILURE_REPORTS
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_processed(processed_dir: Path) -> pd.DataFrame:
    path = processed_dir / DATASET_ID / "telemetry.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run `uv run python -m factorymind.anomaly.cli prepare` first"
        )
    return pd.read_parquet(path, engine="pyarrow")
