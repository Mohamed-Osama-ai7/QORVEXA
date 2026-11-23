"""Task 03B: v1 schemas, v0 compatibility, upgrade, frozen manifest, final mode and ledger."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from qorvexa.evaluation.__main__ import main
from qorvexa.evaluation.jsonl import CaseFileError, load_cases, write_json
from qorvexa.evaluation.provenance import code_fingerprint
from qorvexa.evaluation.schemas import RcaCase, RetrievalCase
from qorvexa.evaluation.splits import (
    SplitError,
    append_ledger,
    build_manifest,
    check_manifest,
    final_gate,
    ledger_summary,
    load_manifest,
    read_ledger,
    select_cases,
)
from qorvexa.evaluation.upgrade import upgrade_file
from tests.test_evaluation_harness import BENCH, RCA_CASES, RET_CASES

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
SRC = Path(__file__).resolve().parents[1] / "src" / "qorvexa"


def rca_v1(case_id: str = "r1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "rca_case/v1",
        "case_id": case_id,
        "task": "rca",
        "provenance": "synthetic_scenario",
        "description": "d",
        "ground_truth_status": "labelled",
        "ground_truth_level": "physics_authored",
        "split": "development",
        "origin": {"kind": "authored_team"},
        "cluster_id": None,
    }
    return base | kw


def ret_v1(case_id: str = "q1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "retrieval_case/v1",
        "case_id": case_id,
        "task": "retrieval",
        "provenance": "project_document_retrieval",
        "query": "compressor duty limit",
        "asset_id": "a",
        "relevant_chunk_prefixes": ["doc-a"],
        "split": "development",
        "origin": {"kind": "authored_team"},
        "cluster_id": None,
        "content_origin": "native",
        "relevance_source": "author_asserted",
    }
    return base | kw


INDEPENDENT = {"kind": "authored_independent", "author_id": "author-x"}


def _write(tmp: Path, rows: list[dict[str, Any]], name: str = "cases.jsonl") -> Path:
    path = tmp / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# --- v0 compatibility and v1 schema rules -------------------------------------------------------


def test_v0_files_still_load_and_never_become_test() -> None:
    rca = load_cases(RCA_CASES, RcaCase)
    ret = load_cases(RET_CASES, RetrievalCase)
    assert {c.effective_split for c in rca} == {"development", "unassigned"}
    assert {c.effective_split for c in ret} == {"unassigned"}
    assert all(c.split is None and c.origin is None for c in rca + ret)  # type: ignore[operator]


def test_v1_fields_are_rejected_in_v0_records() -> None:
    row = rca_v1(schema_version="rca_case/v0")
    with pytest.raises(ValueError, match="require schema_version rca_case/v1"):
        RcaCase.model_validate(row)
    qrow = ret_v1(schema_version="retrieval_case/v0")
    with pytest.raises(ValueError, match="require schema_version retrieval_case/v1"):
        RetrievalCase.model_validate(qrow)


@pytest.mark.parametrize("missing", ["split", "origin", "cluster_id"])
def test_rca_v1_requires_declared_fields(missing: str) -> None:
    row = rca_v1()
    del row[missing]
    with pytest.raises(ValueError, match=f"requires {missing!r}"):
        RcaCase.model_validate(row)


@pytest.mark.parametrize(
    "missing", ["split", "origin", "cluster_id", "content_origin", "relevance_source"]
)
def test_retrieval_v1_requires_declared_fields(missing: str) -> None:
    row = ret_v1()
    del row[missing]
    with pytest.raises(ValueError, match=f"requires {missing!r}"):
        RetrievalCase.model_validate(row)


def test_null_cluster_id_is_allowed_and_means_unknown() -> None:
    assert RcaCase.model_validate(rca_v1(cluster_id=None)).cluster_id is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"evaluation_role": "development"}, "evaluation_role is v0 only"),
        ({"split": "test"}, "authored_independent"),
        ({"split": "fixture"}, "is for, and only for, adversarial_contract"),
        (
            {
                "split": "test",
                "origin": INDEPENDENT,
                "provenance": "metropt3_observed",
                "ground_truth_level": "operator_report_coarse",
                "event_window": {"start": "2020-04-18T00:00:00", "end": "2020-04-18T23:59:00"},
            },
            "cannot be test",
        ),
        (
            {"split": "validation", "origin": {"kind": "derived_from_dev"}},
            "derived_from_dev cases cannot be validation or test",
        ),
        ({"origin": {"kind": "authored_independent"}}, "requires an author_id"),
    ],
)
def test_rca_v1_rules(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        RcaCase.model_validate(rca_v1(**override))


def test_adversarial_contract_must_be_fixture() -> None:
    row = rca_v1(provenance="adversarial_contract", ground_truth_level="contract_fixture")
    with pytest.raises(ValueError, match="fixture"):
        RcaCase.model_validate(row)
    ok = RcaCase.model_validate(row | {"split": "fixture", "origin": {"kind": "contract_fixture"}})
    assert ok.effective_split == "fixture"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"split": "test", "origin": INDEPENDENT}, "annotator_labelled or pooled_adjudicated"),
        ({"split": "test"}, "authored_independent"),
        ({"relevance_source": "annotator_labelled"}, "requires annotator_ids"),
        ({"content_origin": "translated"}, "requires translation_of"),
        ({"translator": "t"}, "only valid for translated"),
        ({"language": "ar"}, "no Arabic characters"),
        ({"source_chunk_sha256": {"x": "0" * 64}}, "listed in source_chunk_ids"),
        ({"split": "fixture"}, "only valid for RCA"),
    ],
)
def test_retrieval_v1_rules(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        RetrievalCase.model_validate(ret_v1(**override))


def test_valid_independent_test_and_native_arabic_retrieval_cases() -> None:
    test_case = RetrievalCase.model_validate(
        ret_v1(
            split="test",
            origin=INDEPENDENT,
            relevance_source="pooled_adjudicated",
            annotator_ids=["ann-1"],
            source_chunk_ids=["doc-a#1"],
            relevant_chunk_prefixes=[],
        )
    )
    assert test_case.effective_split == "test"
    arabic = RetrievalCase.model_validate(ret_v1(language="ar", query="ضغط الهواء"))
    assert arabic.content_origin == "native"


# --- upgrade ------------------------------------------------------------------------------------


def test_upgrade_output_is_valid_deterministic_and_never_promotes(tmp_path: Path) -> None:
    """The upgrade is a pure function of the v0 file; the committed v1 files may have been
    extended or annotated since (clusters, notes), so they are not compared byte for byte."""
    for v0, model in (
        ("rca_cases.v0.jsonl", RcaCase),
        ("retrieval_cases.v0.jsonl", RetrievalCase),
    ):
        first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        upgrade_file(BENCH / v0, first)
        upgrade_file(BENCH / v0, second)
        assert first.read_bytes() == second.read_bytes()
        upgraded = load_cases(first, model)  # type: ignore[type-var]
        assert {c.effective_split for c in upgraded} <= {"development", "unassigned", "fixture"}
        assert all(c.origin is not None and c.origin.author_id is None for c in upgraded)
        assert "test" not in {c.effective_split for c in upgraded}
    rca = load_cases(tmp_path / "a.jsonl", RetrievalCase)
    assert all(c.relevance_source == "inherited_prefix_v0" and c.cluster_id is None for c in rca)
    upgraded_rca = tmp_path / "rca.jsonl"
    upgrade_file(BENCH / "rca_cases.v0.jsonl", upgraded_rca)
    clusters = {c.cluster_id for c in load_cases(upgraded_rca, RcaCase) if c.cluster_id}
    assert clusters == {f"metropt3-f{i}" for i in range(1, 5)}


def test_committed_v1_files_keep_every_v0_case_id_and_stay_development() -> None:
    v0 = {c.case_id for c in load_cases(BENCH / "retrieval_cases.v0.jsonl", RetrievalCase)}
    v1 = {c.case_id: c for c in load_cases(BENCH / "retrieval_cases.v1.jsonl", RetrievalCase)}
    assert v0 <= set(v1)
    assert all(v1[i].effective_split == "development" for i in v0)


def test_committed_v1_files_contain_no_test_or_independent_claims() -> None:
    ret = load_cases(BENCH / "retrieval_cases.v1.jsonl", RetrievalCase)
    assert not [c for c in ret if c.origin and c.origin.kind == "authored_independent"]  # type: ignore[operator]
    assert not [c for c in ret if c.effective_split in ("validation", "test")]  # type: ignore[operator]


def test_metropt3_cases_are_development_and_fixtures_are_fixture() -> None:
    rca = load_cases(BENCH / "rca_cases.v1.jsonl", RcaCase)
    by_prov = {(c.provenance, c.effective_split) for c in rca}
    assert by_prov == {
        ("metropt3_observed", "development"),
        ("synthetic_scenario", "unassigned"),
        ("adversarial_contract", "fixture"),
    }


def test_upgrade_cli_refuses_to_overwrite(tmp_path: Path) -> None:
    dst = tmp_path / "x.jsonl"
    assert main(["upgrade", str(RCA_CASES), str(dst)]) == 0
    assert main(["upgrade", str(RCA_CASES), str(dst)]) == 2
    assert main(["upgrade", str(RCA_CASES), str(dst), "--overwrite"]) == 0


# --- manifest -----------------------------------------------------------------------------------


def _cases(tmp: Path) -> tuple[Path, list[RcaCase]]:
    rows = [
        rca_v1("dev1"),
        rca_v1("test1", split="test", origin=INDEPENDENT),
    ]
    path = _write(tmp, rows)
    return path, load_cases(path, RcaCase)


def test_manifest_detects_frozen_drift_and_split_disagreement(tmp_path: Path) -> None:
    path, cases = _cases(tmp_path)
    manifest = build_manifest(
        [path], {path.name: cases}, version="1.0.0", changelog=[], frozen_files=[path.name]
    )
    assert manifest["assignments"]["rca"] == {"dev1": "development", "test1": "test"}
    assert check_manifest(manifest, tmp_path, cases) == []

    mismatch = json.loads(json.dumps(manifest))
    mismatch["assignments"]["rca"]["dev1"] = "validation"
    assert any("manifest says" in p for p in check_manifest(mismatch, tmp_path, cases))

    path.write_text(path.read_text("utf-8") + "\n", encoding="utf-8")  # frozen file touched
    assert any("changed since" in p for p in check_manifest(manifest, tmp_path, cases))


def test_unfrozen_files_may_change_but_unregistered_test_cases_are_flagged(tmp_path: Path) -> None:
    path, cases = _cases(tmp_path)
    manifest = build_manifest([path], {path.name: cases}, version="1.0.0", changelog=[])
    path.write_text(path.read_text("utf-8") + "\n", encoding="utf-8")
    assert check_manifest(manifest, tmp_path, cases) == []
    manifest["assignments"]["rca"].pop("test1")
    assert any("not registered" in p for p in check_manifest(manifest, tmp_path, cases))


def test_no_code_path_assigns_test_automatically(tmp_path: Path) -> None:
    for name in ("upgrade.py", "splits.py", "jsonl.py", "runner.py"):
        text = (SRC / "evaluation" / name).read_text("utf-8")
        assert '"test"' not in text.replace('"test" in', "") or name == "splits.py"
    assert "test" not in {
        c.effective_split for c in load_cases(BENCH / "rca_cases.v1.jsonl", RcaCase)
    }


# --- select / final mode --------------------------------------------------------------------------


def test_select_cases_refuses_test_without_final_and_requires_test_in_final(tmp_path: Path) -> None:
    _, cases = _cases(tmp_path)
    with pytest.raises(SplitError, match="test cases run only with --final"):
        select_cases(cases, final=False)
    assert [c.case_id for c in select_cases(cases, final=True)] == ["test1"]
    dev_only = [c for c in cases if c.effective_split != "test"]
    assert [c.case_id for c in select_cases(dev_only, final=False)] == ["dev1"]
    with pytest.raises(SplitError, match="no test cases"):
        select_cases(dev_only, final=True)


def test_cli_refuses_test_cases_outside_final_mode(tmp_path: Path) -> None:
    path, _ = _cases(tmp_path)
    preds = tmp_path / "p.jsonl"
    preds.write_text("", encoding="utf-8")
    assert (
        main(
            ["rca", "--cases", str(path), "--predictions", str(preds), "--out", str(tmp_path / "o")]
        )
        == 4
    )
    assert main(["rca-run", "--cases", str(path), "--out", str(tmp_path / "o2")]) == 4
    assert not (tmp_path / "o").exists() and not (tmp_path / "o2").exists()


def test_cli_final_without_test_cases_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, [rca_v1("dev1")])
    assert main(["rca-run", "--cases", str(path), "--out", str(tmp_path / "o"), "--final"]) == 4


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv in a temp repo
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def frozen_repo(tmp_path: Path) -> tuple[Path, Path, list[RcaCase]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    path, cases = _cases(repo)
    manifest_path = repo / "splits.v1.json"
    write_json(
        manifest_path,
        build_manifest(
            [path],
            {path.name: cases},
            version="1.0.0",
            changelog=[{"version": "1.0.0", "note": "test"}],
            frozen_files=[path.name],
            datasets={"metropt3_telemetry_sha256": "abc123"},
        ),
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo, manifest_path, cases


def _gate(repo: Path, manifest: Path | None, cases: list[RcaCase], **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "suite": "rca-run",
        "manifest_path": manifest,
        "cases": cases,
        "dataset_sha256": "abc123",
        "candidate_fingerprint": code_fingerprint(),
        "repo": repo,
    }
    return final_gate(**(args | kw))


@needs_git
def test_final_gate_passes_only_when_every_precondition_holds(
    frozen_repo: tuple[Path, Path, list[RcaCase]],
) -> None:
    repo, manifest, cases = frozen_repo
    facts = _gate(repo, manifest, cases)
    assert facts["code_fingerprint"] == code_fingerprint() and len(facts["git_sha"]) == 40
    assert facts["manifest_version"] == "1.0.0" and facts["dataset_sha256"] == "abc123"


@needs_git
@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"dataset_sha256": "different"}, "differs from the manifest"),
        ({"candidate_fingerprint": "0" * 64}, "candidate code fingerprint"),
        ({"candidate_fingerprint": None}, "--candidate-fingerprint is required"),
        ({"manifest_path": None}, "no frozen splits manifest"),
        ({"suite": "rca"}, "does not support final mode"),
    ],
)
def test_final_gate_refusals(
    frozen_repo: tuple[Path, Path, list[RcaCase]], kw: dict[str, Any], message: str
) -> None:
    repo, manifest, cases = frozen_repo
    with pytest.raises(SplitError, match=re.escape(message)):
        _gate(
            repo,
            kw.get("manifest_path", manifest),
            cases,
            **{k: v for k, v in kw.items() if k != "manifest_path"},
        )


@needs_git
def test_final_gate_refuses_dirty_tree_and_frozen_file_drift(
    frozen_repo: tuple[Path, Path, list[RcaCase]],
) -> None:
    repo, manifest, cases = frozen_repo
    (repo / "scratch.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SplitError, match="working tree is dirty"):
        _gate(repo, manifest, cases)
    (repo / "scratch.txt").unlink()
    case_file = repo / "cases.jsonl"
    case_file.write_text(case_file.read_text("utf-8") + "\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "tamper")  # clean tree, but the frozen file changed
    with pytest.raises(SplitError, match="changed since the manifest was written"):
        _gate(repo, manifest, cases)


@needs_git
def test_final_gate_refuses_unknown_git_state_and_unfrozen_manifest(tmp_path: Path) -> None:
    path, cases = _cases(tmp_path)  # not a git repository
    manifest = tmp_path / "splits.v1.json"
    write_json(
        manifest,
        build_manifest(
            [path],
            {path.name: cases},
            version="1",
            changelog=[],
            datasets={"metropt3_telemetry_sha256": "a"},
        ),
    )
    with pytest.raises(SplitError) as exc:
        _gate(tmp_path, manifest, cases, dataset_sha256="a")
    assert "Git state unknown" in str(
        exc.value
    ) and "case files with test cases are not frozen" in str(exc.value)


def test_ledger_is_append_only_by_convention_and_summarised(tmp_path: Path) -> None:
    ledger = tmp_path / "test_runs.jsonl"
    facts = {"git_sha": "a" * 40, "code_fingerprint": "f1", "manifest_version": "1"}
    append_ledger(ledger, facts, suite="rca-run", case_ids=["b", "a"], results_sha256="r1")
    append_ledger(ledger, facts, suite="rca-run", case_ids=["a"], results_sha256="r2")
    append_ledger(
        ledger,
        facts | {"code_fingerprint": "f2"},
        suite="retrieval",
        case_ids=["q"],
        results_sha256="r3",
    )
    records = read_ledger(ledger)
    assert [r["results_sha256"] for r in records] == ["r1", "r2", "r3"]
    assert records[0]["case_ids"] == ["a", "b"]
    assert ledger_summary(ledger) == {"final_runs": 3, "per_candidate": {"f1": 2, "f2": 1}}
    ledger.write_text(ledger.read_text("utf-8") + "{broken\n", encoding="utf-8")
    with pytest.raises(SplitError, match="invalid JSON"):
        read_ledger(ledger)
    assert ledger_summary(tmp_path / "missing.jsonl") == {"final_runs": 0, "per_candidate": {}}


def test_manifest_cli_roundtrip_and_validate_with_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("rca_cases.v1.jsonl", "retrieval_cases.v1.jsonl"):
        shutil.copy(BENCH / name, tmp_path / name)
    manifest = tmp_path / "splits.v1.json"
    cases = [str(tmp_path / n) for n in ("rca_cases.v1.jsonl", "retrieval_cases.v1.jsonl")]
    assert (
        main(
            [
                "splits",
                "write",
                "--manifest",
                str(manifest),
                "--cases",
                *cases,
                "--version",
                "1.0.0",
                "--note",
                "initial",
            ]
        )
        == 0
    )
    data = load_manifest(manifest)
    assert data["case_files"]["rca_cases.v1.jsonl"]["frozen"] is False
    assert set(data["assignments"]) == {"rca", "retrieval"}
    assert "test" not in set(data["assignments"]["rca"].values()) | set(
        data["assignments"]["retrieval"].values()
    )
    assert main(["splits", "check", "--manifest", str(manifest)]) == 0
    assert main(["validate", cases[0]]) == 0
    assert "consistent with splits.v1.json" in capsys.readouterr().out
    data["assignments"]["rca"]["metropt3-f1-air-leak-report"] = "validation"
    write_json(manifest, data)
    assert main(["splits", "check", "--manifest", str(manifest)]) == 4
    assert main(["validate", cases[0]]) == 4


def test_invalid_v1_file_is_rejected_with_line_numbers(tmp_path: Path) -> None:
    path = _write(tmp_path, [rca_v1("ok"), rca_v1("bad", split="test")])
    with pytest.raises(CaseFileError, match=r"cases.jsonl:2"):
        load_cases(path, RcaCase)


def test_production_code_never_imports_the_evaluation_package() -> None:
    offenders = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if "evaluation" not in p.relative_to(SRC).parts[:1]
        and "qorvexa.evaluation" in p.read_text("utf-8")
    ]
    assert offenders == []
