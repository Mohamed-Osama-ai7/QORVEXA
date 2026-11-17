"""JSONL loading and deterministic writing.

* Benchmark case files are strict: any invalid line raises ``CaseFileError`` listing every
  problem (line numbers included), and duplicate ``case_id`` values are rejected.
* Prediction files are lenient: invalid JSON, schema violations and duplicates are collected as
  per-case or orphan errors; valid records are still returned.
* Writing uses sorted keys, compact separators, UTF-8 and ``\\n`` line endings, so the same
  records always produce byte-identical files.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from factorymind.evaluation.schemas import RcaCase, RcaPrediction, RetrievalCase


class CaseFileError(ValueError):
    """Benchmark ground truth failed strict validation."""


def _iter_lines(path: Path) -> Iterable[tuple[int, str]]:
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if line:
                yield lineno, line


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err["loc"]) or "<record>"
    return f"{loc}: {err['msg']}"


def load_cases[CaseT: (RcaCase, RetrievalCase)](path: Path, model: type[CaseT]) -> list[CaseT]:
    """Strictly load a benchmark case file. Raises ``CaseFileError`` on any defect."""
    problems: list[str] = []
    cases: list[CaseT] = []
    seen: set[str] = set()
    for lineno, line in _iter_lines(path):
        try:
            case = model.model_validate_json(line)
        except ValidationError as exc:
            problems.append(f"{path.name}:{lineno}: {_first_error(exc)}")
            continue
        if case.case_id in seen:
            problems.append(f"{path.name}:{lineno}: duplicate case_id {case.case_id!r}")
            continue
        seen.add(case.case_id)
        cases.append(case)
    if problems:
        raise CaseFileError("; ".join(problems))
    return cases


@dataclass
class PredictionLoad:
    predictions: dict[str, RcaPrediction] = field(default_factory=dict)
    case_errors: dict[str, list[str]] = field(default_factory=dict)
    orphan_errors: list[str] = field(default_factory=list)


def load_rca_predictions(path: Path) -> PredictionLoad:
    """Leniently load system predictions; never raises for malformed content."""
    out = PredictionLoad()
    for lineno, line in _iter_lines(path):
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            out.orphan_errors.append(f"line {lineno}: invalid JSON ({exc.msg})")
            continue
        case_id = raw.get("case_id") if isinstance(raw, dict) else None
        if not isinstance(case_id, str) or not case_id.strip():
            out.orphan_errors.append(f"line {lineno}: record has no usable case_id")
            continue
        case_id = case_id.strip()
        if case_id in out.predictions or case_id in out.case_errors:
            out.predictions.pop(case_id, None)
            out.case_errors.setdefault(case_id, []).append(
                f"line {lineno}: duplicate prediction for case"
            )
            continue
        try:
            out.predictions[case_id] = RcaPrediction.model_validate({**raw, "case_id": case_id})
        except ValidationError as exc:
            out.case_errors[case_id] = [f"line {lineno}: {_first_error(exc)}"]
    return out


def dumps_record(record: BaseModel | Mapping[str, Any]) -> str:
    data = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(path: Path, records: Iterable[BaseModel | Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(dumps_record(record) + "\n")


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(data), sort_keys=True, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
