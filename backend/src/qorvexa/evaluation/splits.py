"""Evaluation splits, the frozen-split manifest, final-mode gating and the final-run ledger.

Roles: ``development`` (may be used for tuning), ``validation`` (model selection),
``test`` (frozen; reported once per candidate), ``fixture`` (tests the scoring code, excluded
from quality aggregates) and ``unassigned`` (legacy / undecided). **No code path assigns
``test``**: a case is a test case only because its case file says so *and* the frozen manifest
registers it. Nothing is promoted automatically.

What this does and does not protect (limits are documented in EVALUATION_STRATEGY.md §E):

* Ordinary runs refuse test cases outright, so test results are never produced casually.
* ``--final`` runs verify a clean Git tree, the frozen manifest and case-file hashes, the
  dataset/corpus hashes and the declared candidate code fingerprint, then append a ledger record.
* The ledger and manifest are plain files in the repository. Anyone who can edit the repository
  can edit them, and a clean tree only means the edits were committed. The mechanism makes
  accidental or casual test use hard and makes deliberate use visible in review and Git history.
  It does **not** make test leakage impossible, and it cannot detect test information that
  leaked through human knowledge, copied queries or documents, or earlier unrecorded runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qorvexa.evaluation.jsonl import dumps_record, sha256_file
from qorvexa.evaluation.provenance import code_fingerprint, git_info
from qorvexa.evaluation.schemas import RcaCase, RetrievalCase

SPLITS: tuple[str, ...] = ("development", "validation", "test", "fixture", "unassigned")
MANIFEST_SCHEMA = "splits/v1"
REQUIRED_DATASET_KEYS: Mapping[str, str] = {
    "rca-run": "metropt3_telemetry_sha256",
    "retrieval": "retrieval_corpus_sha256",
}


class SplitError(ValueError):
    """A split rule was violated (exit code 4)."""


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != MANIFEST_SCHEMA:
        raise SplitError(f"{path.name}: not a {MANIFEST_SCHEMA} manifest")
    return data


def build_manifest(
    case_files: Sequence[Path],
    cases_by_file: Mapping[str, Sequence[RcaCase | RetrievalCase]],
    *,
    version: str,
    changelog: Sequence[Mapping[str, str]],
    frozen_files: Sequence[str] = (),
    datasets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Describe case files and the split of every case. Frozen files are hash-checked later."""
    assignments: dict[str, dict[str, str]] = {}
    file_splits: dict[str, list[str]] = {}
    for path in case_files:
        file_splits[path.name] = sorted({c.effective_split for c in cases_by_file[path.name]})
        for case in cases_by_file[path.name]:
            if case.schema_version.endswith("/v0"):
                continue  # legacy cases carry no split metadata and are never registered
            assignments.setdefault(case.task, {})[case.case_id] = case.effective_split
    return {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_version": version,
        "changelog": [dict(entry) for entry in changelog],
        "case_files": {
            p.name: {
                "sha256": sha256_file(p),
                "frozen": p.name in frozen_files,
                "splits": file_splits[p.name],
            }
            for p in sorted(case_files, key=lambda x: x.name)
        },
        "assignments": {t: dict(sorted(a.items())) for t, a in sorted(assignments.items())},
        "datasets": dict(sorted((datasets or {}).items())),
    }


def check_manifest(
    manifest: Mapping[str, Any], base: Path, cases: Sequence[RcaCase | RetrievalCase]
) -> list[str]:
    """Return problems: hash drift in frozen files, and cases whose split disagrees with (or is
    missing from) the manifest. Non-frozen files may change freely."""
    problems: list[str] = []
    for name, info in sorted(manifest.get("case_files", {}).items()):
        path = base / name
        if info.get("frozen"):
            if not path.is_file():
                problems.append(f"frozen case file {name} is missing")
            elif sha256_file(path) != info.get("sha256"):
                problems.append(f"frozen case file {name} changed since the manifest was written")
    assignments = manifest.get("assignments", {})
    for case in cases:
        if case.schema_version.endswith("/v0"):
            continue  # legacy v0 cases have no split metadata; they can never be test cases
        declared = assignments.get(case.task, {}).get(case.case_id)
        if declared is None:
            if case.effective_split == "test":
                problems.append(f"test case {case.case_id!r} is not registered in the manifest")
        elif declared != case.effective_split:
            problems.append(
                f"case {case.case_id!r}: file says {case.effective_split!r}, manifest says "
                f"{declared!r}"
            )
    return problems


def select_cases(
    cases: Sequence[RcaCase | RetrievalCase], *, final: bool
) -> list[RcaCase | RetrievalCase]:
    """Ordinary runs refuse any test case; final runs evaluate the test cases only."""
    test = [c for c in cases if c.effective_split == "test"]
    if not final:
        if test:
            raise SplitError(
                f"{len(test)} test case(s) present; test cases run only with --final "
                f"(first: {test[0].case_id!r})"
            )
        return list(cases)
    if not test:
        raise SplitError("--final requested but the case files contain no test cases")
    return test


def final_gate(
    *,
    suite: str,
    manifest_path: Path | None,
    cases: Sequence[RcaCase | RetrievalCase],
    dataset_sha256: str | None,
    candidate_fingerprint: str | None,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Verify every precondition of a final run; raise ``SplitError`` listing all failures.

    Checks: clean Git tree; frozen manifest present, valid and consistent with the case files;
    dataset or corpus hash equal to the manifest's; the declared candidate code fingerprint
    equal to the current code. Returns the facts to write into the ledger."""
    problems: list[str] = []
    git = git_info(repo)
    if git["dirty"] is None:
        problems.append("Git state unknown; a final run needs a verifiable clean tree")
    elif git["dirty"]:
        problems.append(f"working tree is dirty ({len(git['dirty_paths'])} path(s))")
    manifest: dict[str, Any] = {}
    if manifest_path is None or not manifest_path.is_file():
        problems.append("no frozen splits manifest")
    else:
        manifest = load_manifest(manifest_path)
        problems += check_manifest(manifest, manifest_path.parent, cases)
        unfrozen = sorted(
            name
            for name, info in manifest.get("case_files", {}).items()
            if "test" in info.get("splits", []) and not info.get("frozen")
        )
        if unfrozen:
            problems.append(f"case files with test cases are not frozen: {unfrozen}")
        if not any("test" in i.get("splits", []) for i in manifest.get("case_files", {}).values()):
            problems.append("manifest lists no case file containing test cases")
        key = REQUIRED_DATASET_KEYS.get(suite)
        expected = manifest.get("datasets", {}).get(key) if key else None
        if key is None:
            problems.append(f"suite {suite!r} does not support final mode")
        elif expected is None:
            problems.append(f"manifest has no dataset hash {key!r}")
        elif dataset_sha256 != expected:
            problems.append(f"{key} differs from the manifest's frozen value")
    current = code_fingerprint()
    if candidate_fingerprint is None:
        problems.append("--candidate-fingerprint is required in final mode")
    elif candidate_fingerprint != current:
        problems.append("candidate code fingerprint does not match the current code")
    if problems:
        raise SplitError("final run refused: " + "; ".join(problems))
    return {
        "git_sha": git["sha"],
        "code_fingerprint": current,
        "manifest_version": manifest.get("manifest_version"),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path else None,
        "dataset_sha256": dataset_sha256,
    }


def append_ledger(
    ledger: Path,
    facts: Mapping[str, Any],
    *,
    suite: str,
    case_ids: Sequence[str],
    results_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one final-run record. Append-only by convention (see module docstring)."""
    record = {
        **facts,
        "suite": suite,
        "case_ids": sorted(case_ids),
        "results_sha256": results_sha256,
        "utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps_record(record) + "\n")
    return record


def read_ledger(ledger: Path) -> list[dict[str, Any]]:
    """Records in file order; malformed lines are reported, not skipped silently."""
    if not ledger.is_file():
        return []
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SplitError(f"{ledger.name}:{lineno}: invalid JSON ({exc.msg})") from exc
        if not isinstance(item, dict):
            raise SplitError(f"{ledger.name}:{lineno}: record is not an object")
        records.append(item)
    return records


def ledger_summary(ledger: Path) -> dict[str, Any]:
    """How often the test set was evaluated, in total and per candidate code fingerprint."""
    records = read_ledger(ledger)
    per_candidate: dict[str, int] = {}
    for rec in records:
        key = str(rec.get("code_fingerprint"))
        per_candidate[key] = per_candidate.get(key, 0) + 1
    return {"final_runs": len(records), "per_candidate": dict(sorted(per_candidate.items()))}
