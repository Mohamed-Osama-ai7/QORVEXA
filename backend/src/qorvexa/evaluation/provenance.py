"""Evaluation provenance: what code, data and model produced a run.

Two artifacts, deliberately separated:

* ``manifest.json`` is deterministic: input/dataset/model hashes, a code fingerprint, component
  hashes and the run configuration. Same inputs + same code => same bytes.
* ``run_info.json`` is environment metadata that legitimately varies: git SHA and dirty status,
  Python/platform, creation time, and the SHA-256 of the deterministic outputs.

The engine and package builder carry no explicit version constants, so their identity is the
SHA-256 of their source files (``component_hashes``); nothing is added to production code.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import qorvexa
from qorvexa.evaluation.jsonl import sha256_file

PACKAGE_ROOT = Path(qorvexa.__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[2]

# Source files whose content identifies the evaluated engine.
COMPONENT_FILES: Mapping[str, str] = {
    "evidence_package_builder": "anomaly/evidence_package.py",
    "rca_engine": "anomaly/rca.py",
    "rca_scoring": "anomaly/rca_eval.py",
    "anomaly_service": "anomaly/service.py",
    "harness_schemas": "evaluation/schemas.py",
    "harness_rca": "evaluation/rca.py",
}


def code_fingerprint() -> str:
    """SHA-256 over every ``.py`` file in the package (sorted relative paths + bytes)."""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        digest.update(rel.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def component_hashes() -> dict[str, str]:
    return {name: sha256_file(PACKAGE_ROOT / rel) for name, rel in sorted(COMPONENT_FILES.items())}


def display_path(path: Path, *, run_dir: Path | None = None) -> str:
    """Repo-relative posix path if inside the repo, ``run_dir/<name>`` inside the run
    directory, else absolute posix."""
    resolved = path.resolve()
    if run_dir is not None and resolved.is_relative_to(run_dir.resolve()):
        return "run_dir/" + resolved.relative_to(run_dir.resolve()).as_posix()
    if resolved.is_relative_to(REPO_ROOT):
        return resolved.relative_to(REPO_ROOT).as_posix()
    return resolved.as_posix()


def resolve_display_path(text: str, run_dir: Path) -> Path:
    if text.startswith("run_dir/"):
        return run_dir / text.removeprefix("run_dir/")
    path = Path(text)
    return path if path.is_absolute() else REPO_ROOT / path


def input_entry(role: str, path: Path, *, run_dir: Path | None = None) -> dict[str, str]:
    return {"role": role, "path": display_path(path, run_dir=run_dir), "sha256": sha256_file(path)}


def _git(args: Sequence[str], cwd: Path) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell, read-only
            ["git", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_info(repo: Path | None = None) -> dict[str, Any]:
    """Git SHA and dirty status. Unknown values are ``None`` with a reason; never raises."""
    cwd = repo or REPO_ROOT
    sha = _git(["rev-parse", "HEAD"], cwd)
    status = _git(["status", "--porcelain", "--untracked-files=all"], cwd)
    if sha is None or status is None:
        return {"sha": None, "dirty": None, "dirty_paths": [], "reason": "git unavailable"}
    paths = sorted(line[3:].strip() for line in status.splitlines() if line.strip())
    return {
        "sha": sha.strip(),
        "dirty": bool(paths),
        "dirty_paths": paths,
        "branch": (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd) or "").strip() or None,
    }


def dataset_info(processed_dir: Path, dataset_id: str = "metropt3") -> dict[str, Any]:
    """Hash the processed telemetry and cross-check the declared source-archive hash."""
    root = processed_dir / dataset_id
    telemetry = root / "telemetry.parquet"
    manifest_path = root / "manifest.json"
    info: dict[str, Any] = {
        "dataset_id": dataset_id,
        "telemetry_path": telemetry.resolve().as_posix(),
        "telemetry_sha256": sha256_file(telemetry),
        "manifest_sha256": None,
        "archive_sha256_declared": None,
        "archive_sha256_verified": False,
    }
    if manifest_path.is_file():
        info["manifest_sha256"] = sha256_file(manifest_path)
        info["archive_sha256_declared"] = json.loads(manifest_path.read_text("utf-8")).get(
            "archive_sha256"
        )
    return info


def model_info(
    models_dir: Path, model_name: str, bundle: Any, card: Mapping[str, Any]
) -> dict[str, Any]:
    """Identity of the loaded detector bundle: version, content hash, card hash."""
    version_dir = models_dir / model_name / str(bundle.version)
    artifact = version_dir / "model.joblib"
    card_path = version_dir / "model_card.json"
    digest = sha256_file(artifact)
    return {
        "model_name": model_name,
        "version": str(bundle.version),
        "detector": str(bundle.detector_name),
        "artifact_path": artifact.resolve().as_posix(),
        "artifact_sha256": digest,
        "artifact_sha256_matches_card": digest == card.get("artifact_sha256"),
        "model_card_sha256": sha256_file(card_path),
    }


def run_info(
    out_dir: Path, deterministic_files: Sequence[str], *, now: datetime | None = None
) -> dict[str, Any]:
    return {
        "created_utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git_info(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "outputs_sha256": {name: sha256_file(out_dir / name) for name in deterministic_files},
        "note": "Environment metadata; excluded from determinism checks. Metric outputs live in "
        "results.jsonl and summary.json.",
    }


def verify_run(run_dir: Path, *, check_code: bool = False) -> list[str]:
    """Recompute every recorded hash; return a list of mismatches (empty = verified).

    Checks the outputs recorded in ``run_info.json`` and the inputs, dataset and model
    artifacts recorded in ``manifest.json``. Missing files are mismatches. With ``check_code``,
    also require the current code fingerprint to equal the recorded one (any later edit to the
    package makes this fail, so it is opt-in)."""
    problems: list[str] = []

    def check(label: str, path: Path, expected: str | None) -> None:
        if expected is None:
            return
        if not path.is_file():
            problems.append(f"{label}: missing {path}")
        elif sha256_file(path) != expected:
            problems.append(f"{label}: hash mismatch for {path}")

    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    for item in manifest.get("inputs", []):
        check(f"input {item['role']}", resolve_display_path(item["path"], run_dir), item["sha256"])
    ds = manifest.get("dataset")
    if ds:
        check("dataset telemetry", Path(ds["telemetry_path"]), ds["telemetry_sha256"])
    model = manifest.get("model")
    if model and model.get("artifact_path"):
        check("model artifact", Path(model["artifact_path"]), model["artifact_sha256"])
    info = json.loads((run_dir / "run_info.json").read_text("utf-8"))
    for name, digest in info.get("outputs_sha256", {}).items():
        check(f"output {name}", run_dir / name, digest)
    if check_code and manifest.get("code", {}).get("fingerprint") != code_fingerprint():
        problems.append("code fingerprint differs from the code that produced the run")
    return problems
