"""Task 02B: QORVEXA evaluation harness (schemas, JSONL, RCA scoring, retrieval, CLI smoke)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from qorvexa.anomaly.evidence_package import build_event_evidence_package
from qorvexa.anomaly.rca import analyze_event_evidence
from qorvexa.evaluation.__main__ import main
from qorvexa.evaluation.jsonl import (
    CaseFileError,
    load_cases,
    load_rca_predictions,
    write_jsonl,
)
from qorvexa.evaluation.rca import (
    RCA_METRICS,
    prediction_from_assessment,
    predictions_from_service,
    score_rca_case,
)
from qorvexa.evaluation.retrieval import (
    RetrievalSystem,
    reciprocal_rank_at_k,
    run_retrieval_case,
)
from qorvexa.evaluation.schemas import RcaCase, RetrievalCase
from tests.auth_helpers import ASSET
from tests.test_evidence_foundation import _make_wp_a_service

BENCH = Path(__file__).resolve().parents[1] / "benchmarks" / "qorvexa"
RCA_CASES = BENCH / "rca_cases.v0.jsonl"
RCA_PREDS = BENCH / "rca_predictions.fixture.v0.jsonl"
RET_CASES = BENCH / "retrieval_cases.v0.jsonl"


def _rca_case(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "rca_case/v0",
        "case_id": "c1",
        "task": "rca",
        "provenance": "adversarial_contract",
        "description": "d",
        "ground_truth_status": "labelled",
        "ground_truth_level": "contract_fixture",
    }
    return base | kw


def _write(tmp_path: Path, rows: list[Any], name: str = "f.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


# --- strict ground truth -------------------------------------------------------------------


def test_starter_benchmarks_validate() -> None:
    rca = load_cases(RCA_CASES, RcaCase)
    ret = load_cases(RET_CASES, RetrievalCase)
    assert len(rca) == 16 and len(ret) == 13
    provs = {c.provenance for c in rca}
    assert provs == {"metropt3_observed", "synthetic_scenario", "adversarial_contract"}
    observed = [c for c in rca if c.provenance == "metropt3_observed"]
    assert all(c.ground_truth_level == "operator_report_coarse" for c in observed)
    assert all(c.expected_supporting_evidence is None for c in observed)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"expected_supporting_evidence": ["load duty"]}, "canonical identifiers"),
        ({"expected_missing_checks": [""]}, "canonical identifiers"),
        ({"critical_contradictions": ["made_up_kind"]}, "unknown contradiction kinds"),
        (
            {"ground_truth_status": "insufficient", "expected_categories": ["air_leakage"]},
            "do not invent ground truth",
        ),
        (
            {"expected_categories": ["air_leakage"], "unacceptable_categories": ["air_leakage"]},
            "both expected and unacceptable",
        ),
        ({"unexpected_field": 1}, "Extra inputs"),
        ({"provenance": "metropt3_observed"}, "event_window"),
    ],
)
def test_invalid_ground_truth_rejects_file(
    tmp_path: Path, override: dict[str, Any], message: str
) -> None:
    path = _write(tmp_path, [_rca_case(case_id="ok"), _rca_case(**override)])
    with pytest.raises(CaseFileError, match=message) as exc:
        load_cases(path, RcaCase)
    assert "f.jsonl:2" in str(exc.value)


def test_duplicate_case_ids_and_bad_json_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, [_rca_case(), _rca_case(), "{not json"])
    with pytest.raises(CaseFileError) as exc:
        load_cases(path, RcaCase)
    assert "duplicate case_id" in str(exc.value) and "f.jsonl:3" in str(exc.value)


def test_retrieval_case_scope_rules(tmp_path: Path) -> None:
    base = {
        "schema_version": "retrieval_case/v0",
        "case_id": "r1",
        "task": "retrieval",
        "query": "q",
        "asset_id": "a",
    }
    bad = [
        base | {"provenance": "project_document_retrieval", "relevant_chunk_prefixes": []},
        base | {"provenance": "out_of_scope_probe", "relevant_chunk_prefixes": ["x"]},
        base | {"provenance": "project_document_retrieval", "relevant_chunk_prefixes": [" x"]},
    ]
    for row in bad:
        with pytest.raises(CaseFileError):
            load_cases(_write(tmp_path, [row]), RetrievalCase)


# --- lenient predictions -------------------------------------------------------------------


def test_prediction_loading_never_aborts_batch(tmp_path: Path) -> None:
    good = {
        "schema_version": "rca_prediction/v0",
        "case_id": "a",
        "producer": "t",
        "abstained": False,
        "primary_category": "air_leakage",
    }
    path = _write(
        tmp_path,
        [
            good,
            "{broken",
            {"producer": "t"},
            good | {"case_id": "b", "primary_category": "boiler_explosion"},
            good | {"case_id": "c"},
            good | {"case_id": "c"},
        ],
    )
    load = load_rca_predictions(path)
    assert set(load.predictions) == {"a"}
    assert "primary_category" in load.case_errors["b"][0]
    assert "duplicate" in load.case_errors["c"][0]
    assert len(load.orphan_errors) == 2


def test_missing_and_invalid_predictions_score_nothing() -> None:
    case = RcaCase.model_validate(_rca_case(expected_abstain=False))
    missing = score_rca_case(case, None)
    invalid = score_rca_case(case, None, ["line 1: bad"])
    assert missing.status == "missing_prediction" and invalid.status == "invalid_prediction"
    for res in (missing, invalid):
        assert {m.state for m in res.metrics.values()} == {"no_prediction"}
        assert all(m.value is None for m in res.metrics.values())


# --- end-to-end smoke on the starter fixtures (CI) -----------------------------------------


def _results(out: Path) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text("utf-8").splitlines()]
    return {r["case_id"]: r for r in rows}


def _val(row: dict[str, Any], metric: str) -> Any:
    m = row["metrics"][metric]
    return m["value"] if m["state"] == "scored" else m["state"]


def test_rca_cli_smoke_and_adversarial_outcomes(tmp_path: Path) -> None:
    out = tmp_path / "run"
    argv = ["rca", "--cases", str(RCA_CASES), "--predictions", str(RCA_PREDS), "--out", str(out)]
    assert main(argv) == 0
    res = _results(out)
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    manifest = json.loads((out / "manifest.json").read_text("utf-8"))

    assert summary["overall"]["status"] == {
        "invalid_prediction": 1,
        "missing_prediction": 6,
        "scored": 9,
    }
    assert len(manifest["orphan_errors"]) == 2  # broken JSON line + unknown case id

    ignored = res["adv-critical-mentioned-ignored"]
    assert _val(ignored, "critical_contradiction_disclosure") == 1.0
    assert _val(ignored, "critical_contradiction_handling") == "undecided"
    assert _val(ignored, "unacceptable_hypothesis") == 1.0
    assert _val(res["adv-critical-hidden"], "critical_contradiction_handling") == 0.0
    assert _val(res["adv-partial-disclosure"], "critical_contradiction_handling") == 0.0
    assert _val(res["adv-safe-abstention"], "critical_contradiction_handling") == 1.0
    assert _val(res["adv-missing-evidence"], "missing_check_recall") == 0.5
    assert _val(res["adv-missing-evidence"], "missing_check_precision") == 0.5
    malformed = res["adv-malformed-output-keys"]
    assert malformed["discarded_keys"] == 5
    assert _val(malformed, "supporting_evidence_precision") == 1.0
    assert res["adv-invalid-prediction"]["status"] == "invalid_prediction"
    assert _val(res["adv-unsupported-conclusion"], "abstention_correct") == 0.0
    assert _val(res["adv-insufficient-ground-truth"], "category_match") == "unlabelled"
    assert res["metropt3-f1-air-leak-report"]["status"] == "missing_prediction"
    for row in res.values():
        assert _val(row, "recommendation_accuracy") in ("unavailable", "no_prediction")
        assert _val(row, "hitl_compliance") in ("unavailable", "no_prediction")
    # Means only average scored outcomes; undecided is counted, never averaged.
    handling = summary["overall"]["metrics"]["critical_contradiction_handling"]
    assert handling["states"]["undecided"] == 1 and handling["mean"] == 0.5


def test_runs_are_byte_identical(tmp_path: Path) -> None:
    outs = [tmp_path / "a", tmp_path / "b"]
    for out in outs:
        main(["rca", "--cases", str(RCA_CASES), "--predictions", str(RCA_PREDS), "--out", str(out)])
    for name in ("results.jsonl", "summary.json", "manifest.json"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes()


def test_validate_cli_reports_invalid_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["validate", str(RCA_CASES)]) == 0
    assert main(["validate", str(RET_CASES)]) == 0
    bad = _write(tmp_path, [_rca_case(expected_supporting_evidence=[" "])])
    assert main(["validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().err


def test_retrieval_cli_smoke_without_dataset(tmp_path: Path) -> None:
    out = tmp_path / "ret"
    raw = tmp_path / "no-raw"
    assert (
        main(["retrieval", "--cases", str(RET_CASES), "--out", str(out), "--raw-dir", str(raw)])
        == 0
    )
    res = _results(out)
    assert len(res) == 13
    # The MetroPT-3 PDF is not indexed without the dataset: unavailable, not a zero score.
    pdf = res["ret-metropt3-pdf-sensors"]
    assert pdf["status"] == "no_relevant_in_corpus"
    assert _val(pdf, "recall_at_5") == "unavailable"
    assert all(res[c]["status"] == "scored" for c in res if c.startswith("oos-"))


# --- retrieval scoring with an injected (fake) system --------------------------------------


def _ret_case(**kw: Any) -> RetrievalCase:
    base = {
        "schema_version": "retrieval_case/v0",
        "case_id": "r1",
        "task": "retrieval",
        "provenance": "project_document_retrieval",
        "query": "q",
        "asset_id": "a",
        "relevant_chunk_prefixes": ["doc-a"],
    }
    return RetrievalCase.model_validate(base | kw)


def test_retrieval_scoring_sanitizes_system_output() -> None:
    system = RetrievalSystem(
        name="fake",
        search=lambda q, a, k: ["", None, "x-1", "doc-a#1", 7, "doc-a#2"],
        corpus=lambda a: {"x-1", "doc-a#1", "doc-a#2", "doc-a#3", "doc-a#4"},
    )
    res = run_retrieval_case(_ret_case(), system)
    assert res.discarded_keys == 3
    assert res.metrics["hit_rate_at_5"].value == 1.0
    assert res.metrics["recall_at_5"].value == 0.5
    assert res.metrics["reciprocal_rank_at_10"].value == 0.5
    assert res.metrics["out_of_scope_abstention"].state == "not_applicable"


def test_reciprocal_rank_edges() -> None:
    assert reciprocal_rank_at_k(["a", "a", "b"], {"b"}, 2) == 0.5
    assert reciprocal_rank_at_k(["a", "c", "b"], {"b"}, 2) == 0.0
    assert reciprocal_rank_at_k(["a"], set(), 5) is None


# --- adapter from real RCA output ----------------------------------------------------------


def test_prediction_from_real_rca_output(tmp_path: Path) -> None:
    svc, _, _ = _make_wp_a_service(tmp_path)
    case = RcaCase.model_validate(
        _rca_case(
            case_id="wp-a-clean-leak",
            provenance="synthetic_scenario",
            ground_truth_level="physics_authored",
            event_window={"start": "2020-01-06T06:00:00", "end": "2020-01-06T09:00:00"},
            expected_categories=["air_leakage"],
        )
    )
    preds = predictions_from_service(svc, [case], asset_id=ASSET)
    assert len(preds) == 1
    pred = preds[0]
    assert pred.producer == "qorvexa.anomaly.rca"
    assert pred.package_critical_contradictions is not None
    res = score_rca_case(case, pred)
    assert res.status == "scored"
    assert res.metrics["category_match"].value == 1.0
    assert set(res.metrics) == set(RCA_METRICS)

    # Same output via the direct adapter; written records reload as valid predictions.
    pkg = build_event_evidence_package(
        svc,
        asset_id=ASSET,
        event_start=pd.Timestamp("2020-01-06 06:00"),
        event_end=pd.Timestamp("2020-01-06 09:00"),
        raised_at=pd.Timestamp("2020-01-06 06:00"),
    )
    direct = prediction_from_assessment(case.case_id, analyze_event_evidence(pkg), package=pkg)
    assert direct == pred
    path = tmp_path / "preds.jsonl"
    write_jsonl(path, [direct])
    assert load_rca_predictions(path).predictions[case.case_id] == direct
