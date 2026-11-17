"""Task 03A: evaluation provenance, hash verification, determinism and engine adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from factorymind.evaluation.__main__ import main
from factorymind.evaluation.jsonl import (
    CaseFileError,
    load_cases,
    load_rca_predictions,
    sha256_file,
    write_jsonl,
)
from factorymind.evaluation.provenance import (
    COMPONENT_FILES,
    code_fingerprint,
    git_info,
    verify_run,
)
from factorymind.evaluation.rca import (
    RCA_METRICS,
    predictions_from_service,
    run_engine,
    score_rca_cases,
)
from factorymind.evaluation.schemas import RcaCase
from tests.auth_helpers import ASSET
from tests.test_evaluation_harness import RCA_CASES, RCA_PREDS, _rca_case, _write
from tests.test_evidence_foundation import _make_wp_a_service

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _run_rca(out: Path, preds: Path = RCA_PREDS) -> None:
    assert (
        main(["rca", "--cases", str(RCA_CASES), "--predictions", str(preds), "--out", str(out)])
        == 0
    )


def _load(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


# --- completeness --------------------------------------------------------------------------


def test_manifest_and_run_info_are_complete(tmp_path: Path) -> None:
    out = tmp_path / "run"
    _run_rca(out)
    manifest, info = _load(out / "manifest.json"), _load(out / "run_info.json")
    assert {i["role"] for i in manifest["inputs"]} == {"cases", "predictions"}
    assert all(len(i["sha256"]) == 64 for i in manifest["inputs"])
    assert manifest["code"]["fingerprint"] == code_fingerprint()
    assert set(manifest["code"]["components"]) == set(COMPONENT_FILES)
    assert manifest["interpretation"]["held_out_cases"] == 0
    assert "do not measure diagnostic accuracy" in manifest["interpretation"]["statement"]
    assert set(info["outputs_sha256"]) == {"results.jsonl", "summary.json", "manifest.json"}
    assert {"sha", "dirty", "dirty_paths"} <= set(info["git"])
    assert info["created_utc"].endswith("Z") and info["python"]
    # Environment metadata never leaks into the deterministic artifacts.
    for name in ("results.jsonl", "summary.json", "manifest.json"):
        text = (out / name).read_text("utf-8")
        assert info["created_utc"] not in text and "dirty_paths" not in text


def test_runs_are_deterministic_except_run_info(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _run_rca(a)
    _run_rca(b)
    for name in ("results.jsonl", "summary.json", "manifest.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


# --- hash verification ---------------------------------------------------------------------


def test_verify_accepts_untouched_run_and_detects_tampering(tmp_path: Path) -> None:
    out = tmp_path / "run"
    preds = tmp_path / "preds.jsonl"
    shutil.copy(RCA_PREDS, preds)
    _run_rca(out, preds)
    assert verify_run(out, check_code=True) == []
    assert main(["verify", str(out), "--check-code"]) == 0

    (out / "results.jsonl").write_text("tampered\n", encoding="utf-8")
    assert any("output results.jsonl" in p for p in verify_run(out))
    assert main(["verify", str(out)]) == 1

    _run_rca(out, preds)
    preds.write_text(preds.read_text("utf-8") + "\n", encoding="utf-8")
    preds.write_text(preds.read_text("utf-8").replace("air_leakage", "sensor_malfunction"))
    assert any("input predictions" in p for p in verify_run(out))


def test_verify_detects_missing_input_and_code_drift(tmp_path: Path) -> None:
    out = tmp_path / "run"
    preds = tmp_path / "preds.jsonl"
    shutil.copy(RCA_PREDS, preds)
    _run_rca(out, preds)
    manifest = _load(out / "manifest.json")
    manifest["code"]["fingerprint"] = "0" * 64
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_run(out) != []  # manifest itself is a recorded output
    assert any("code fingerprint" in p for p in verify_run(out, check_code=True))
    preds.unlink()
    assert any("missing" in p for p in verify_run(out))


# --- dirty tree reporting ------------------------------------------------------------------


@needs_git
def test_git_info_reports_clean_dirty_and_untracked(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv in a temp repo
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    clean = git_info(tmp_path)
    assert clean["dirty"] is False and clean["dirty_paths"] == [] and len(clean["sha"]) == 40

    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    dirty = git_info(tmp_path)
    assert dirty["dirty"] is True
    assert dirty["dirty_paths"] == ["a.txt", "new.txt"]
    assert dirty["sha"] == clean["sha"]


def test_git_info_unknown_is_explicit_not_clean(tmp_path: Path) -> None:
    info = git_info(tmp_path)  # not a repository
    assert info["sha"] is None and info["dirty"] is None
    assert info["reason"] == "git unavailable"


# --- engine adapter: real code path, per-case isolation -----------------------------------


def _case(case_id: str, start: str, end: str, **kw: Any) -> RcaCase:
    return RcaCase.model_validate(
        _rca_case(
            case_id=case_id,
            provenance="synthetic_scenario",
            ground_truth_level="physics_authored",
            event_window={"start": start, "end": end},
            **kw,
        )
    )


def test_run_engine_keeps_provenance_and_handles_empty_windows(tmp_path: Path) -> None:
    svc, _, _ = _make_wp_a_service(tmp_path)
    good = _case(
        "good", "2020-01-06T06:00:00", "2020-01-06T09:00:00", expected_categories=["air_leakage"]
    )
    no_data = _case("no-data", "2031-01-01T00:00:00", "2031-01-01T03:00:00")
    no_window = RcaCase.model_validate(_rca_case(case_id="no-window"))
    run = run_engine(svc, [no_data, good, no_window], asset_id=ASSET)

    assert {p.case_id for p in run.predictions} | set(run.errors) == {"no-data", "good"}
    by_pred = {p.case_id: p for p in run.predictions}
    assert by_pred["good"].package_critical_contradictions is not None
    if "no-data" in by_pred:  # the engine answers a window without data by abstaining
        assert by_pred["no-data"].abstained
    ev = {e["case_id"]: e for e in run.evidence}["good"]
    assert len(ev["package_sha256"]) == 64 and ev["rca_id"]
    assert ev["provenance"]["primary_model_version"] and ev["quality"]["overall_status"]

    path = tmp_path / "preds.jsonl"
    write_jsonl(path, run.predictions)
    by_id = {r.case_id: r for r in score_rca_cases([good, no_window], load_rca_predictions(path))}
    assert by_id["good"].status == "scored"
    assert by_id["good"].details["evaluation_role"] == "unspecified"
    assert by_id["no-window"].status == "missing_prediction"


def test_run_engine_isolates_a_failing_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from factorymind.anomaly import evidence_package

    real = evidence_package.build_event_evidence_package

    def flaky(service: Any, **kw: Any) -> Any:
        if kw["event_start"].year == 2031:
            raise ValueError("synthetic failure")
        return real(service, **kw)

    monkeypatch.setattr(evidence_package, "build_event_evidence_package", flaky)
    svc, _, _ = _make_wp_a_service(tmp_path)
    boom = _case("boom", "2031-01-01T00:00:00", "2031-01-01T03:00:00", expected_abstain=False)
    good = _case("good", "2020-01-06T06:00:00", "2020-01-06T09:00:00")
    run = run_engine(svc, [boom, good], asset_id=ASSET)
    assert run.errors == {"boom": "ValueError: synthetic failure"}
    assert [p.case_id for p in run.predictions] == ["good"]

    path = tmp_path / "p.jsonl"
    write_jsonl(path, run.predictions)
    load = load_rca_predictions(path)
    load.case_errors["boom"] = [f"engine error: {run.errors['boom']}"]
    by_id = {r.case_id: r for r in score_rca_cases([boom, good], load)}
    assert by_id["boom"].status == "invalid_prediction"
    assert "engine error" in by_id["boom"].errors[0]
    assert {m.state for m in by_id["boom"].metrics.values()} == {"no_prediction"}
    assert by_id["good"].status == "scored"
    with pytest.raises(RuntimeError, match="boom"):
        predictions_from_service(svc, [boom], asset_id=ASSET)


def test_rca_run_reports_unavailable_data_and_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "rca-run",
            "--cases",
            str(RCA_CASES),
            "--out",
            str(tmp_path / "o"),
            "--data-dir",
            str(tmp_path / "none"),
        ]
    )
    assert rc == 3
    assert "UNAVAILABLE" in capsys.readouterr().err
    assert not (tmp_path / "o" / "results.jsonl").exists()


# --- honesty about development and unlabelled cases ---------------------------------------


def test_metropt3_cases_are_development_and_cannot_be_held_out(tmp_path: Path) -> None:
    observed = [c for c in load_cases(RCA_CASES, RcaCase) if c.provenance == "metropt3_observed"]
    assert len(observed) == 4 and {c.evaluation_role for c in observed} == {"development"}
    row = _rca_case(
        case_id="x",
        provenance="metropt3_observed",
        ground_truth_level="operator_report_coarse",
        event_window={"start": "2020-04-18T00:00:00", "end": "2020-04-18T23:59:00"},
        evaluation_role="held_out",
    )
    with pytest.raises(ValueError, match="cannot be labelled held_out"):
        RcaCase.model_validate(row)
    with pytest.raises(CaseFileError, match="cannot be labelled held_out"):
        load_cases(_write(tmp_path, [row]), RcaCase)


def test_observed_case_without_evidence_labels_reports_unlabelled(tmp_path: Path) -> None:
    svc, _, _ = _make_wp_a_service(tmp_path)
    case = RcaCase.model_validate(
        _rca_case(
            case_id="observed-like",
            provenance="metropt3_observed",
            ground_truth_status="partial",
            ground_truth_level="operator_report_coarse",
            evaluation_role="development",
            event_window={"start": "2020-01-06T06:00:00", "end": "2020-01-06T09:00:00"},
            expected_categories=["air_leakage", "concurrent_conditions"],
        )
    )
    run = run_engine(svc, [case], asset_id=ASSET)
    path = tmp_path / "p.jsonl"
    write_jsonl(path, run.predictions)
    res = score_rca_cases([case], load_rca_predictions(path))[0]
    for name in (
        "supporting_evidence_recall",
        "supporting_evidence_precision",
        "missing_check_recall",
        "contradicting_evidence_disclosure",
        "abstention_correct",
    ):
        assert res.metrics[name].state == "unlabelled", name
    assert res.metrics["recommendation_accuracy"].state == "unavailable"
    assert res.metrics["hitl_compliance"].state == "unavailable"
    assert res.details["evaluation_role"] == "development"
    assert set(res.metrics) == set(RCA_METRICS)
    assert sha256_file(path)  # artifact is a regular, hashable file
