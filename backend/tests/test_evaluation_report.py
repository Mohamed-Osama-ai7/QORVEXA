"""Unit tests for deterministic markdown report generator in report.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qorvexa.evaluation.report import generate_markdown_report


def _setup_mock_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_test"
    run_dir.mkdir(parents=True)

    summary_data = {
        "summary_schema": "summary/v1",
        "overall": {
            "cases": 4,
            "metrics": {
                "hit_rate_at_5": {
                    "mean": 1.0,
                    "n_scored": 4,
                    "states": {"scored": 4},
                    "conservative": {
                        "measured_rate": 1.0,
                        "conservative_rate": 0.8,
                        "scored_n": 4,
                        "undecided_n": 1,
                    },
                    "interval": {
                        "point_estimate": 1.0,
                        "lower": 0.85,
                        "upper": 1.0,
                        "method": "wilson",
                        "state": "scored",
                        "n_samples": 4,
                    },
                },
                "recall_at_5": {
                    "mean": 0.75,
                    "n_scored": 4,
                    "states": {"scored": 4},
                    "interval": {
                        "point_estimate": 0.75,
                        "lower": 0.50,
                        "upper": 0.90,
                        "method": "bootstrap_bca",
                        "state": "scored",
                        "n_samples": 4,
                    },
                },
            },
        },
        "by_split": {
            "development": {
                "cases": 4,
                "metrics": {"hit_rate_at_5": {"mean": 1.0, "n_scored": 4, "states": {"scored": 4}}},
            }
        },
        "by_provenance": {
            "project_document_retrieval": {
                "cases": 4,
                "metrics": {"hit_rate_at_5": {"mean": 1.0, "n_scored": 4, "states": {"scored": 4}}},
            }
        },
    }

    manifest_data = {
        "suite": "retrieval",
        "system": "hybrid_rrf",
        "case_count": 4,
        "harness_version": "0.1.0",
        "code": {"fingerprint": "abc1234567890"},
        "interpretation": {
            "statement": "Starter benchmark: development exploration only.",
        },
    }

    run_info_data = {
        "git_sha": "def456",
        "dirty": False,
        "platform": "win32",
        "python_version": "3.12.10",
        "created_utc": "2026-11-17T21:00:00Z",
    }

    (run_dir / "summary.json").write_text(json.dumps(summary_data), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")
    (run_dir / "run_info.json").write_text(json.dumps(run_info_data), encoding="utf-8")

    return run_dir


def test_generate_markdown_report_deterministic(tmp_path: Path) -> None:
    run_dir = _setup_mock_run(tmp_path)

    report_1 = generate_markdown_report(run_dir, include_env=False)
    report_2 = generate_markdown_report(run_dir, include_env=False)

    assert report_1 == report_2
    assert "2026-11-17T21:00:00Z" not in report_1
    assert "3.12.10" not in report_1

    assert "# QORVEXA Evaluation Report — RETRIEVAL" in report_1
    assert "Total Cases Evaluated:** `4`" in report_1
    assert "`hit_rate_at_5`" in report_1
    assert "Conservative Binary Rates" in report_1
    assert "Limitations & Epistemic Boundaries" in report_1


def test_generate_markdown_report_includes_env_when_requested(tmp_path: Path) -> None:
    run_dir = _setup_mock_run(tmp_path)

    report_env = generate_markdown_report(run_dir, include_env=True)
    assert "Environment Metadata" in report_env
    assert "2026-11-17T21:00:00Z" in report_env
    assert "3.12.10" in report_env


def test_generate_markdown_report_missing_summary_raises(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        generate_markdown_report(empty_dir)
