"""Aggregation and run directories.

A run directory contains ``results.jsonl`` (one ``CaseResult`` per case, in case-file order),
``summary.json`` (per-metric means and state counts, overall and per provenance) and
``manifest.json`` (inputs with SHA-256, config, code fingerprint, dataset/model identity,
orphan errors) and ``run_info.json`` (git SHA, dirty status, environment, output hashes). The
first three contain no timestamps, so re-running on the same inputs and code gives
byte-identical files; ``run_info.json`` is environment metadata and is excluded from that.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from qorvexa.evaluation.intervals import (
    SAMPLING_NOTE,
    conservative_binary,
    metric_interval,
)
from qorvexa.evaluation.jsonl import write_json, write_jsonl
from qorvexa.evaluation.provenance import (
    code_fingerprint,
    component_hashes,
    input_entry,
    run_info,
)
from qorvexa.evaluation.schemas import CaseResult
from qorvexa.knowledge.eval import macro_average

HARNESS_VERSION = "0.1.0"


def _rounded(data: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in data.items()}


def _summarize_group(
    results: Sequence[CaseResult], metric_names: Sequence[str], *, intervals: bool = False
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for name in metric_names:
        outcomes = [r.metrics[name] for r in results]
        mean = macro_average(o.value for o in outcomes if o.state == "scored")
        entry: dict[str, Any] = {
            "mean": None if mean is None else round(mean, 6),
            "n_scored": sum(1 for o in outcomes if o.state == "scored"),
            "states": dict(sorted(Counter(o.state for o in outcomes).items())),
        }
        conservative = conservative_binary(name, results)
        if conservative is not None:
            entry["conservative"] = conservative
        if intervals:
            entry["interval"] = _rounded(metric_interval(name, results).model_dump(mode="json"))
        metrics[name] = entry
    return {
        "cases": len(results),
        "status": dict(sorted(Counter(r.status for r in results).items())),
        "metrics": metrics,
    }


def summarize(results: Sequence[CaseResult], metric_names: Sequence[str]) -> dict[str, Any]:
    """Means are over ``scored`` outcomes only; every other state is counted, never averaged."""
    by_prov: dict[str, list[CaseResult]] = {}
    for r in results:
        by_prov.setdefault(r.provenance, []).append(r)
    by_split: dict[str, list[CaseResult]] = {}
    for r in results:
        by_split.setdefault(r.split or "unassigned", []).append(r)
    return {
        "summary_schema": "summary/v1",
        "interval_note": SAMPLING_NOTE,
        "overall": _summarize_group(results, metric_names),
        # Fixtures test the scoring code and get no intervals; every other split does.
        "by_split": {
            k: _summarize_group(v, metric_names, intervals=k != "fixture")
            for k, v in sorted(by_split.items())
        },
        "by_provenance": {k: _summarize_group(v, metric_names) for k, v in sorted(by_prov.items())},
        "discarded_keys": sum(r.discarded_keys for r in results),
    }


def write_run(
    out_dir: Path,
    *,
    suite: str,
    results: Sequence[CaseResult],
    metric_names: Sequence[str],
    inputs: Sequence[tuple[str, Path]],
    system: str,
    orphan_errors: Sequence[str] = (),
    config: Mapping[str, Any] | None = None,
    manifest_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``results.jsonl``, ``summary.json``, ``manifest.json`` (all deterministic) and
    ``run_info.json`` (environment metadata, may vary between identical runs)."""
    summary = summarize(results, metric_names)
    write_jsonl(out_dir / "results.jsonl", results)
    write_json(out_dir / "summary.json", summary)
    manifest: dict[str, Any] = {
        "suite": suite,
        "harness_version": HARNESS_VERSION,
        "result_schema": "eval_result/v0",
        "system": system,
        "inputs": [input_entry(role, path, run_dir=out_dir) for role, path in inputs],
        "config": dict(config or {}),
        "code": {"fingerprint": code_fingerprint(), "components": component_hashes()},
        "case_count": len(results),
        "orphan_errors": list(orphan_errors),
        "note": (
            "Starter benchmark: small, partly synthetic, not statistically powered. "
            "See docs/qorvexa/EVALUATION_STRATEGY.md section D."
        ),
    }
    manifest.update(manifest_extra or {})
    write_json(out_dir / "manifest.json", manifest)
    deterministic = ["results.jsonl", "summary.json", "manifest.json"]
    deterministic += [
        n for n in sorted(manifest.get("extra_outputs", [])) if (out_dir / n).is_file()
    ]
    write_json(out_dir / "run_info.json", run_info(out_dir, deterministic))
    return summary
