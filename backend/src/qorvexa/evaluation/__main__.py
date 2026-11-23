"""Command line for the QORVEXA evaluation harness.

    validate   <cases.jsonl>                      strict case-file validation (v0 and v1)
    upgrade    <v0.jsonl> <v1.jsonl>              deterministic v0 -> v1 upgrade (never promotes)
    splits     write|check --manifest M ...       frozen split manifest
    rca        --cases C --predictions P --out D  score supplied predictions
    rca-run    --cases C --out D [--final ...]    run the RCA engine on real MetroPT-3 windows
    retrieval  --cases C --out D [--final ...]    run retrieval cases on the in-process index
    verify     <run_dir> [--check-code]           recompute every recorded hash
    audit      ...                                heuristic leakage audit (not proof)
    report     <run_dir>                          deterministic Markdown report

Exit codes: 0 success; 1 invalid case file or hash mismatch; 2 usage error; 3 data or model
unavailable; 4 split rule violated (e.g. test cases without a valid ``--final`` run).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from qorvexa.evaluation.jsonl import (
    CaseFileError,
    load_cases,
    load_rca_predictions,
    sha256_file,
    write_json,
    write_jsonl,
)
from qorvexa.evaluation.provenance import dataset_info, model_info, verify_run
from qorvexa.evaluation.rca import RCA_METRICS, run_engine, score_rca_cases
from qorvexa.evaluation.retrieval import (
    RETRIEVAL_METRICS,
    corpus_fingerprint,
    run_retrieval_cases,
    system_from_index,
)
from qorvexa.evaluation.runner import write_run
from qorvexa.evaluation.schemas import RcaCase, RetrievalCase
from qorvexa.evaluation.splits import (
    SplitError,
    append_ledger,
    build_manifest,
    check_manifest,
    final_gate,
    load_manifest,
    select_cases,
)
from qorvexa.evaluation.upgrade import upgrade_file

DEFAULT_MANIFEST = "splits.v1.json"
DEFAULT_LEDGER = "test_runs.jsonl"


def _task_of(path: Path) -> str | None:
    first = path.read_text(encoding="utf-8").lstrip().split("\n", 1)[0]
    try:
        head = json.loads(first) if first else {}
    except json.JSONDecodeError:
        return None
    return head.get("task") if isinstance(head, dict) else None


def _load_any(path: Path) -> list[RcaCase] | list[RetrievalCase]:
    if _task_of(path) == "retrieval":
        return load_cases(path, RetrievalCase)
    return load_cases(path, RcaCase)


def _validate(args: argparse.Namespace) -> int:
    cases = _load_any(args.path)
    kind = "RetrievalCase" if cases and isinstance(cases[0], RetrievalCase) else "RcaCase"
    versions = sorted({c.schema_version for c in cases})
    splits = dict(sorted(Counter(c.effective_split for c in cases).items()))
    print(f"OK: {len(cases)} {kind} records in {args.path} versions={versions} splits={splits}")
    manifest = args.splits
    if manifest is None and (args.path.parent / DEFAULT_MANIFEST).is_file():
        manifest = args.path.parent / DEFAULT_MANIFEST
    if manifest is not None:
        problems = check_manifest(load_manifest(manifest), manifest.parent, cases)
        if problems:
            raise SplitError("; ".join(problems))
        print(f"OK: consistent with {manifest.name}")
    return 0


def _upgrade(args: argparse.Namespace) -> int:
    if args.dst.exists() and not args.overwrite:
        print(f"REFUSED: {args.dst} exists (use --overwrite)", file=sys.stderr)
        return 2
    count = upgrade_file(args.src, args.dst)
    print(f"OK: upgraded {count} records to {args.dst}")
    return 0


def _splits(args: argparse.Namespace) -> int:
    if args.action == "check":
        manifest = load_manifest(args.manifest)
        cases: list[RcaCase | RetrievalCase] = []
        for name in manifest["case_files"]:
            cases += _load_any(args.manifest.parent / name)
        problems = check_manifest(manifest, args.manifest.parent, cases)
        for problem in problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
        if not problems:
            print(f"OK: {args.manifest.name} consistent with {len(cases)} cases")
        return 4 if problems else 0
    paths = [Path(p) for p in args.cases]
    by_file = {p.name: _load_any(p) for p in paths}
    datasets = dict(item.split("=", 1) for item in args.dataset)
    changelog: list[dict[str, str]] = []
    if args.manifest.is_file():
        changelog = list(load_manifest(args.manifest).get("changelog", []))
    changelog.append({"version": args.version, "note": args.note})
    manifest_data = build_manifest(
        paths,
        by_file,
        version=args.version,
        changelog=changelog,
        frozen_files=args.freeze,
        datasets=datasets,
    )
    write_json(args.manifest, manifest_data)
    print(f"OK: wrote {args.manifest} (version {args.version}, frozen: {args.freeze or 'none'})")
    return 0


def interpretation(cases: Sequence[RcaCase]) -> dict[str, object]:
    """Say what the results may and may not be used to claim."""
    splits = Counter(c.effective_split for c in cases)
    test = splits.get("test", 0)
    return {
        "splits": dict(sorted(splits.items())),
        "held_out_cases": test,
        "statement": (
            "No test (held-out) cases were evaluated: results are development/contract checks "
            "and do not measure diagnostic accuracy."
            if test == 0
            else "Frozen test cases were evaluated in final mode; this is still a small starter "
            "benchmark and intervals measure sampling variability only."
        ),
    }


def _manifest_for(args: argparse.Namespace) -> Path | None:
    if args.splits is not None:
        return Path(args.splits)
    candidate = args.cases.parent / DEFAULT_MANIFEST
    return candidate if candidate.is_file() else None


def _prepare(args: argparse.Namespace, all_cases: Sequence[Any]) -> tuple[list[Any], Path | None]:
    """Refuse test cases outside final mode; check any manifest against the case files."""
    selected = select_cases(all_cases, final=args.final)
    manifest_path = _manifest_for(args)
    if manifest_path is not None and manifest_path.is_file():
        problems = check_manifest(load_manifest(manifest_path), manifest_path.parent, all_cases)
        if problems:
            raise SplitError("manifest inconsistent: " + "; ".join(problems))
    return selected, manifest_path


def _finish_final(
    args: argparse.Namespace,
    facts: dict[str, Any] | None,
    manifest_path: Path | None,
    *,
    suite: str,
    cases: Sequence[Any],
) -> None:
    if facts is None or manifest_path is None:
        return
    ledger = args.ledger or manifest_path.parent / DEFAULT_LEDGER
    append_ledger(
        ledger,
        facts,
        suite=suite,
        case_ids=[c.case_id for c in cases],
        results_sha256=sha256_file(args.out / "results.jsonl"),
    )
    print(f"LEDGER: recorded final run in {ledger}")


def _rca(args: argparse.Namespace) -> int:
    args.final = False  # predictions suite has no final mode
    cases, _ = _prepare(args, load_cases(args.cases, RcaCase))
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


def _rca_run(args: argparse.Namespace) -> int:
    from qorvexa.anomaly.service import (
        AnomalyService,
        ModelUnavailableError,
        load_bundle,
        load_warning_bundle,
    )

    all_cases = load_cases(args.cases, RcaCase)
    selected, manifest_path = _prepare(args, all_cases)
    cases = [c for c in selected if c.event_window is not None]
    skipped = sorted(c.case_id for c in selected if c.event_window is None)
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

    facts = None
    if args.final:
        facts = final_gate(
            suite="rca-run",
            manifest_path=manifest_path,
            cases=all_cases,
            dataset_sha256=dataset["telemetry_sha256"],
            candidate_fingerprint=args.candidate_fingerprint,
        )

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
        system="qorvexa.anomaly.rca",
        orphan_errors=load.orphan_errors,
        config={"asset_id": args.asset_id, "model_name": args.model_name, "final": args.final},
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
    _finish_final(args, facts, manifest_path, suite="rca-run", cases=cases)
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
    from qorvexa.knowledge.corpus import build_verified_corpus
    from qorvexa.knowledge.rag import HybridRagIndex

    all_cases = load_cases(args.cases, RetrievalCase)
    cases, manifest_path = _prepare(args, all_cases)
    index = HybridRagIndex(build_verified_corpus(args.raw_dir))
    corpus_hash = corpus_fingerprint(index)
    facts = None
    if args.final:
        facts = final_gate(
            suite="retrieval",
            manifest_path=manifest_path,
            cases=all_cases,
            dataset_sha256=corpus_hash,
            candidate_fingerprint=args.candidate_fingerprint,
        )
    system = system_from_index(index, mode=args.mode)
    results = run_retrieval_cases(cases, system)
    summary = write_run(
        args.out,
        suite="retrieval",
        results=results,
        metric_names=RETRIEVAL_METRICS,
        inputs=[("cases", args.cases)],
        system=system.name,
        config={"mode": args.mode, "raw_dir": args.raw_dir.as_posix(), "final": args.final},
        manifest_extra={
            "corpus": {"fingerprint_sha256": corpus_hash, "chunks": len(index.chunks)},
            "interpretation": {
                "splits": dict(sorted(Counter(c.effective_split for c in cases).items())),
            },
        },
    )
    _finish_final(args, facts, manifest_path, suite="retrieval", cases=cases)
    print(json.dumps(summary["overall"]["status"], sort_keys=True))
    return 0


def _audit(args: argparse.Namespace) -> int:
    """Wire the CLI to ``leakage.audit_leakage`` (heuristic findings, never proof)."""
    from qorvexa.evaluation.leakage import DEFAULT_TRAINING_PERIODS, audit_leakage

    rca = load_cases(args.rca_cases, RcaCase) if args.rca_cases else []
    retrieval = load_cases(args.retrieval_cases, RetrievalCase) if args.retrieval_cases else []
    corpus: dict[str, str] | None = None
    if retrieval and args.raw_dir.exists():
        from qorvexa.knowledge.corpus import build_verified_corpus

        corpus = {c.chunk_id: c.text for c in build_verified_corpus(args.raw_dir)}
    periods: Sequence[dict[str, str]] = DEFAULT_TRAINING_PERIODS
    if args.model_card is not None:
        card = json.loads(args.model_card.read_text(encoding="utf-8"))
        periods = [
            {"name": name, "start": f"{card[key][0]}T00:00:00", "end": f"{card[key][1]}T23:59:59"}
            for key, name in (
                ("training_period", "training"),
                ("calibration_period", "calibration"),
            )
            if key in card
        ]
    every_case: list[RcaCase | RetrievalCase] = [*rca, *retrieval]
    dev_authors = sorted(
        {
            c.origin.author_id
            for c in every_case
            if c.origin is not None
            and c.origin.author_id is not None
            and c.effective_split in ("development", "unassigned")
        }
    )
    report = audit_leakage(
        rca_cases=rca,
        retrieval_cases=retrieval,
        corpus_chunks=corpus,
        training_periods=periods,
        dev_author_ids=dev_authors,
    )
    if args.out is not None:
        write_json(args.out, report.model_dump(mode="json"))
    print(report.heuristics_disclaimer)
    print(f"findings: {report.total_findings} by severity {report.findings_by_severity}")
    if corpus is None and retrieval:
        print("note: no corpus supplied (--raw-dir), so query-to-document copying was not checked")
    return 1 if args.strict and report.has_critical else 0


def _report(args: argparse.Namespace) -> int:
    """Write the deterministic report; environment metadata goes to a separate file."""
    from qorvexa.evaluation.report import generate_markdown_report

    out = args.out or args.run_dir / "report.md"
    try:
        text = generate_markdown_report(args.run_dir)
        env_text = (
            generate_markdown_report(args.run_dir, include_env=True) if args.with_env else None
        )
    except FileNotFoundError as exc:
        print(f"MISSING: {exc}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"OK: wrote {out}")
    if env_text is not None:
        env_out = out.with_name("report.env.md")
        env_out.write_text(env_text, encoding="utf-8", newline="\n")
        print(f"OK: wrote {env_out} (includes environment metadata)")
    return 0


def _add_split_args(p: argparse.ArgumentParser, *, final: bool) -> None:
    p.add_argument(
        "--splits", type=Path, default=None, help="splits manifest (default: beside cases)"
    )
    if final:
        p.add_argument("--final", action="store_true", help="evaluate the frozen test cases")
        p.add_argument(
            "--candidate-fingerprint", default=None, help="code fingerprint being tested"
        )
        p.add_argument("--ledger", type=Path, default=None, help="final-run ledger file")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factorymind.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="strictly validate a benchmark case file")
    v.add_argument("path", type=Path)
    v.add_argument("--splits", type=Path, default=None)

    u = sub.add_parser("upgrade", help="upgrade a v0 case file to v1 (never promotes cases)")
    u.add_argument("src", type=Path)
    u.add_argument("dst", type=Path)
    u.add_argument("--overwrite", action="store_true")

    s = sub.add_parser("splits", help="write or check the frozen split manifest")
    s.add_argument("action", choices=["write", "check"])
    s.add_argument("--manifest", type=Path, required=True)
    s.add_argument("--cases", nargs="*", default=[], help="case files (write)")
    s.add_argument("--freeze", nargs="*", default=[], help="case file names to freeze (write)")
    s.add_argument("--dataset", nargs="*", default=[], help="name=sha256 pairs (write)")
    s.add_argument("--version", default="1.0.0")
    s.add_argument("--note", default="manifest written")

    r = sub.add_parser("rca", help="score RCA predictions against RCA cases")
    r.add_argument("--cases", type=Path, required=True)
    r.add_argument("--predictions", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    _add_split_args(r, final=False)

    rr = sub.add_parser("rca-run", help="run the RCA engine on real MetroPT-3 windows and score it")
    rr.add_argument("--cases", type=Path, required=True)
    rr.add_argument("--out", type=Path, required=True)
    rr.add_argument("--data-dir", type=Path, default=Path("D:/QORVEXA/data"))
    rr.add_argument(
        "--models-dir", type=Path, default=None, help="default: <data-dir>/../artifacts/models"
    )
    rr.add_argument("--model-name", default="metropt3-anomaly")
    rr.add_argument("--asset-id", default="metropt3-apu-1")
    _add_split_args(rr, final=True)

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
    _add_split_args(q, final=True)

    a = sub.add_parser("audit", help="heuristic leakage audit (findings are not proof either way)")
    a.add_argument("--rca-cases", type=Path, default=None)
    a.add_argument("--retrieval-cases", type=Path, default=None)
    a.add_argument("--raw-dir", type=Path, default=Path("does-not-exist"))
    a.add_argument("--out", type=Path, default=None, help="write the audit as JSON")
    a.add_argument("--model-card", type=Path, default=None, help="detector model_card.json")
    a.add_argument("--strict", action="store_true", help="exit 1 on critical findings")

    rp = sub.add_parser("report", help="deterministic Markdown report from a run directory")
    rp.add_argument("run_dir", type=Path)
    rp.add_argument("--out", type=Path, default=None, help="default: <run_dir>/report.md")
    rp.add_argument("--with-env", action="store_true", help="also write report.env.md")

    args = parser.parse_args(argv)
    try:
        handlers = {
            "validate": _validate,
            "upgrade": _upgrade,
            "splits": _splits,
            "rca": _rca,
            "rca-run": _rca_run,
            "verify": _verify,
            "retrieval": _retrieval,
            "audit": _audit,
            "report": _report,
        }
        return handlers[args.command](args)
    except CaseFileError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    except SplitError as exc:
        print(f"SPLIT RULE: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
