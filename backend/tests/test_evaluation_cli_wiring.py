"""Task 03B: CLI wiring of the leakage audit and report generator, v0 compatibility.

The audit and report modules are owned by another contributor; these tests only check how the CLI
uses them (exit codes, file layout, determinism, environment separation) and that old v0 files and
runs keep working."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from qorvexa.evaluation.__main__ import main
from qorvexa.evaluation.provenance import verify_run
from tests.test_evaluation_harness import BENCH, RCA_CASES, RCA_PREDS, RET_CASES
from tests.test_evaluation_splits import INDEPENDENT, ret_v1

V1_RCA = BENCH / "rca_cases.v1.jsonl"
V1_RET = BENCH / "retrieval_cases.v1.jsonl"
TEST_KW: dict[str, Any] = {
    "split": "test",
    "origin": INDEPENDENT,
    "relevance_source": "annotator_labelled",
    "annotator_ids": ["ann-1"],
}


def _cases(tmp: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp / "q.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _rca_run(out: Path, cases: Path = V1_RCA) -> None:
    argv = ["rca", "--cases", str(cases), "--predictions", str(RCA_PREDS), "--out", str(out)]
    assert main(argv) == 0


# --- audit ---------------------------------------------------------------------------------------


def test_audit_cli_flags_planted_leak_and_strict_controls_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [
        ret_v1("dev1", query="unloaded tp3 pressure decay slope"),
        ret_v1("test1", query="Unloaded tp3 pressure decay slope", **TEST_KW),
    ]
    path, out = _cases(tmp_path, rows), tmp_path / "audit.json"
    assert main(["audit", "--retrieval-cases", str(path), "--out", str(out)]) == 0
    assert main(["audit", "--retrieval-cases", str(path), "--strict"]) == 1
    data = json.loads(out.read_text("utf-8"))
    assert data["schema_version"] == "leakage_report/v1" and data["has_critical"] is True
    assert "not constitute definitive proof" in data["heuristics_disclaimer"]
    printed = capsys.readouterr().out
    assert "do not constitute definitive proof" in printed
    assert "query-to-document copying was not checked" in printed  # honesty: skipped check named


@pytest.mark.parametrize(("rca", "ret"), [(V1_RCA, V1_RET), (RCA_CASES, RET_CASES)])
def test_audit_cli_accepts_v1_and_v0_starter_files_without_critical_findings(
    tmp_path: Path, rca: Path, ret: Path
) -> None:
    out = tmp_path / "a.json"
    argv = [
        "audit",
        "--rca-cases",
        str(rca),
        "--retrieval-cases",
        str(ret),
        "--out",
        str(out),
        "--strict",
    ]
    assert main(argv) == 0
    assert json.loads(out.read_text("utf-8"))["has_critical"] is False


def test_audit_cli_reads_training_periods_from_a_model_card(tmp_path: Path) -> None:
    card = tmp_path / "model_card.json"
    card.write_text(json.dumps({"training_period": ["2020-04-10", "2020-04-20"]}), encoding="utf-8")
    out = tmp_path / "a.json"
    assert (
        main(["audit", "--rca-cases", str(V1_RCA), "--model-card", str(card), "--out", str(out)])
        == 0
    )
    assert "training_window_overlap" in json.loads(out.read_text("utf-8"))["findings_by_category"]


# --- report ---


def test_report_is_deterministic_and_keeps_environment_metadata_separate(tmp_path: Path) -> None:
    out = tmp_path / "run"
    _rca_run(out)
    assert main(["report", str(out), "--with-env"]) == 0
    first = (out / "report.md").read_bytes()
    assert main(["report", str(out), "--with-env"]) == 0
    assert (out / "report.md").read_bytes() == first
    info = json.loads((out / "run_info.json").read_text("utf-8"))
    text = first.decode("utf-8")
    for leaked in (info["created_utc"], info["python"], info["platform"]):
        assert leaked not in text
    assert info["git"]["sha"] is None or info["git"]["sha"] not in text
    env = (out / "report.env.md").read_text("utf-8")
    assert info["created_utc"] in env or info["python"] in env


def test_report_states_no_held_out_evidence_and_shows_counts_and_states(tmp_path: Path) -> None:
    out = tmp_path / "run"
    _rca_run(out)
    assert main(["report", str(out)]) == 0
    text = (out / "report.md").read_text("utf-8")
    assert "No test (held-out) cases were evaluated" in text
    assert "do not measure diagnostic accuracy" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    assert rows
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert any(re.fullmatch(r"\d+", c) or "n=" in c or "N=" in c for c in cells), row
    for state in ("no_prediction", "unlabelled", "undecided", "unavailable"):
        assert state in text  # distinctions preserved, not collapsed
    assert "development" in text and "fixture" in text


def test_report_does_not_present_development_cases_as_test(tmp_path: Path) -> None:
    out = tmp_path / "run"
    _rca_run(out)
    assert main(["report", str(out)]) == 0
    text = (out / "report.md").read_text("utf-8")
    assert "### Split: `test`" not in text and "Split: `development` (4 case(s))" in text


def test_report_on_missing_run_dir_fails_cleanly(tmp_path: Path) -> None:
    assert main(["report", str(tmp_path / "nope")]) == 1


# --- v0 compatibility ---


def test_v0_files_still_run_and_verify_and_report(tmp_path: Path) -> None:
    out = tmp_path / "v0run"
    _rca_run(out, RCA_CASES)
    assert verify_run(out, check_code=True) == []
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["by_split"]["development"]["cases"] == 4  # v0 role maps to development
    assert set(summary["by_split"]) == {"development", "unassigned"}
    assert main(["report", str(out)]) == 0


def test_v1_and_v0_runs_score_identically(tmp_path: Path) -> None:
    a, b = tmp_path / "v0", tmp_path / "v1"
    _rca_run(a, RCA_CASES)
    _rca_run(b, V1_RCA)

    def metrics(run: Path) -> dict[str, Any]:
        rows = [json.loads(x) for x in (run / "results.jsonl").read_text("utf-8").splitlines()]
        return {r["case_id"]: r["metrics"] for r in rows}

    assert metrics(a) == metrics(b)


def test_validate_reports_versions_and_splits(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(V1_RCA)]) == 0
    out = capsys.readouterr().out
    assert "rca_case/v1" in out and "'fixture': 10" in out and "'test'" not in out
    assert main(["validate", str(RCA_CASES)]) == 0
    assert "rca_case/v0" in capsys.readouterr().out
