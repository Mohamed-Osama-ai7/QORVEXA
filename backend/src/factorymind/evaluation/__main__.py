"""Command line for the QORVEXA evaluation harness.

    uv run python -m factorymind.evaluation validate benchmarks/qorvexa/rca_cases.v0.jsonl
    uv run python -m factorymind.evaluation rca --cases ... --predictions ... --out DIR
    uv run python -m factorymind.evaluation retrieval --cases ... --out DIR [--raw-dir DIR]

Exit codes: 0 success; 1 invalid case file or hash mismatch; 2 usage error; 3 data or model
unavailable.

    uv run python -m factorymind.evaluation rca-run --cases ... --out DIR [--data-dir ...]
    uv run python -m factorymind.evaluation verify DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from factorymind.evaluation.jsonl import (
    CaseFileError,
    load_cases,
    load_rca_predictions,
    sha256_file,
    write_jsonl,
)
from factorymind.evaluation.provenance import dataset_info, model_info, verify_run
from factorymind.evaluation.rca import RCA_METRICS, run_engine, score_rca_cases
from factorymind.evaluation.retrieval import (
    RETRIEVAL_METRICS,
    run_retrieval_cases,
    system_from_index,
)
from factorymind.evaluation.runner import write_run
from factorymind.evaluation.schemas import RcaCase, RetrievalCase


def _validate(path: Path) -> int:
    first = path.read_text(encoding="utf-8").lstrip().split("\n", 1)[0]
    try:
        head = json.loads(first) if first else {}
    except json.JSONDecodeError:
        head = {}
    task = head.get("task") if isinstance(head, dict) else None
    if task == "retrieval":
        count, name = len(load_cases(path, RetrievalCase)), "RetrievalCase"
    else:
        count, name = len(load_cases(path, RcaCase)), "RcaCase"
    print(f"OK: {count} {name} records in {path}")
    return 0


def _rca(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases, RcaCase)
    load = load_rca_predictions(args.predictions)
    results = score_rca_cases(cases, load)
    producers = sorted({p.producer for p in load.predictions.values()})
    summary = write_run(
        args.out,
        suite="rca",
        results=results,
        metric_names=RCA_METRICS,
        inputs=[("cases", args.cases), ("predictions", args.predictions)],
        system=",".join(producers) or "none",
        orphan_errors=load.orphan_errors,
        manifest_extra={"interpretation": interpretation(cases)},
    )
    print(json.dumps(summary["overall"]["status"], sort_keys=True))
    return 0


def interpretation(cases: Sequence[RcaCase]) -> dict[str, object]:
    """Say what the results may and may not be used to claim."""
    roles = Counter(c.evaluation_role for c in cases)
    return {
        "evaluation_roles": dict(sorted(roles.items())),
        "held_out_cases": roles.get("held_out", 0),
        "statement": (
            "No held-out evidence: results are development/contract checks and do not "
            "measure diagnostic accuracy."
            if roles.get("held_out", 0) == 0
            else "Held-out cases are present; still a starter set, not statistically powered."
        ),
    }


def _rca_run(args: argparse.Namespace) -> int:
    from factorymind.anomaly.service import (
        AnomalyService,
        ModelUnavailableError,
        load_bundle,
        load_warning_bundle,
    )

    all_cases = load_cases(args.cases, RcaCase)
    cases = [c for c in all_cases if c.event_window is not None]
    skipped = sorted(c.case_id for c in all_cases if c.event_window is None)
    models_dir = (
        args.data_dir.parent / "artifacts" / "models"
        if args.models_dir is None
        else args.models_dir
    )
    processed_dir = args.data_dir / "processed"
    try:
        bundle, card = load_bundle(models_dir, args.model_name)
        warning = load_warning_bundle(models_dir, args.model_name, bundle)
        service = AnomalyService(bundle, card, processed_dir, warning_bundle=warning)
        dataset = dataset_info(processed_dir)
        model = model_info(models_dir, args.model_name, bundle, card)
    except (ModelUnavailableError, OSError) as exc:
        print(f"UNAVAILABLE: {exc}", file=sys.stderr)
        return 3

    engine = run_engine(service, cases, asset_id=args.asset_id)
    args.out.mkdir(parents=True, exist_ok=True)
    predictions_path = args.out / "predictions.jsonl"
    evidence_path = args.out / "evidence_provenance.jsonl"
    write_jsonl(predictions_path, engine.predictions)
    write_jsonl(evidence_path, engine.evidence)

    load = load_rca_predictions(predictions_path)
    for case_id, message in engine.errors.items():
        load.case_errors[case_id] = [f"engine error: {message}"]
    results = score_rca_cases(cases, load)
    summary = write_run(
        args.out,
        suite="rca-real",
        results=results,
        metric_names=RCA_METRICS,
        inputs=[("cases", args.cases)],
        system="factorymind.anomaly.rca",
        orphan_errors=load.orphan_errors,
        config={"asset_id": args.asset_id, "model_name": args.model_name},
        manifest_extra={
            "dataset": dataset,
            "model": model,
            "interpretation": interpretation(cases),
            "cases_not_run": {
                "count": len(skipped),
                "reason": "no event_window; the engine cannot be run on them",
                "case_ids": skipped,
            },
            "outputs_generated": [
                {"file": "predictions.jsonl", "sha256": sha256_file(predictions_path)},
                {"file": "evidence_provenance.jsonl", "sha256": sha256_file(evidence_path)},
            ],
            "extra_outputs": ["predictions.jsonl", "evidence_provenance.jsonl"],
        },
    )
    print(json.dumps(summary["overall"]["status"], sort_keys=True))
    return 0


def _verify(args: argparse.Namespace) -> int:
    problems = verify_run(args.run_dir, check_code=args.check_code)
    for problem in problems:
        print(f"MISMATCH: {problem}", file=sys.stderr)
    if not problems:
        print(f"VERIFIED: {args.run_dir}")
    return 1 if problems else 0


def _retrieval(args: argparse.Namespace) -> int:
    from factorymind.knowledge.corpus import build_verified_corpus
    from factorymind.knowledge.rag import HybridRagIndex

    cases = load_cases(args.cases, RetrievalCase)
    index = HybridRagIndex(build_verified_corpus(args.raw_dir))
    system = system_from_index(index, mode=args.mode)
    results = run_retrieval_cases(cases, system)
    summary = write_run(
        args.out,
        suite="retrieval",
        results=results,
        metric_names=RETRIEVAL_METRICS,
        inputs=[("cases", args.cases)],
        system=system.name,
        config={"mode": args.mode, "raw_dir": args.raw_dir.as_posix()},
    )
    print(json.dumps(summary["overall"]["status"], sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factorymind.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="strictly validate a benchmark case file")
    v.add_argument("path", type=Path)
    r = sub.add_parser("rca", help="score RCA predictions against RCA cases")
    r.add_argument("--cases", type=Path, required=True)
    r.add_argument("--predictions", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    rr = sub.add_parser("rca-run", help="run the RCA engine on real MetroPT-3 windows and score it")
    rr.add_argument("--cases", type=Path, required=True)
    rr.add_argument("--out", type=Path, required=True)
    rr.add_argument("--data-dir", type=Path, default=Path("D:/FactoryMind/data"))
    rr.add_argument(
        "--models-dir", type=Path, default=None, help="default: <data-dir>/../artifacts/models"
    )
    rr.add_argument("--model-name", default="metropt3-anomaly")
    rr.add_argument("--asset-id", default="metropt3-apu-1")
    vv = sub.add_parser("verify", help="recompute and check every hash recorded for a run")
    vv.add_argument("run_dir", type=Path)
    vv.add_argument("--check-code", action="store_true", help="also require unchanged code")
    q = sub.add_parser("retrieval", help="run retrieval cases on the in-process index")
    q.add_argument("--cases", type=Path, required=True)
    q.add_argument("--out", type=Path, required=True)
    q.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("does-not-exist"),
        help="raw data dir; MetroPT-3 PDF chunks are indexed only if found here",
    )
    q.add_argument(
        "--mode", choices=["bm25_only", "dense_svd_only", "hybrid_rrf"], default="hybrid_rrf"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args.path)
        if args.command == "rca":
            return _rca(args)
        if args.command == "rca-run":
            return _rca_run(args)
        if args.command == "verify":
            return _verify(args)
        return _retrieval(args)
    except CaseFileError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
