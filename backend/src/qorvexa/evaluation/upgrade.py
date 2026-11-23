"""Deterministic v0 -> v1 upgrade of benchmark case files.

The upgrade adds the v1 fields with conservative values and never promotes a case:

* nothing becomes ``validation`` or ``test``;
* authors are never invented (``author_id`` stays ``null``);
* ``cluster_id`` is set only where a dependence unit is actually known (one MetroPT-3 failure
  report = one cluster); everywhere else it is ``null`` (dependence unknown, so no confidence
  interval can be computed for the metric);
* v0 files are left untouched; the v1 file is written next to them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

INHERITED_RETRIEVAL_SOURCE = "knowledge/eval.py:DEFAULT_EVAL_CASES and DEFAULT_OOS_EVAL_QUERIES"


def upgrade_rca_v0(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "rca_case/v0":
        raise ValueError(f"not an rca_case/v0 record: {record.get('case_id')!r}")
    out = {k: v for k, v in record.items() if k != "evaluation_role"}
    out["schema_version"] = "rca_case/v1"
    provenance = record["provenance"]
    role = record.get("evaluation_role", "unspecified")
    origin: dict[str, Any]
    if provenance == "adversarial_contract":
        split, origin = "fixture", {"kind": "contract_fixture"}
    elif provenance == "metropt3_observed":
        split = "development" if role == "development" else "unassigned"
        ref = str(record.get("input_ref") or "")
        origin = {"kind": "observed_dataset", "derived_from": ref or None}
    else:
        split, origin = "unassigned", {"kind": "authored_team"}
    out["split"] = split
    out["origin"] = origin
    ref = str(record.get("input_ref") or "")
    out["cluster_id"] = (
        "metropt3-" + ref.rsplit(":", 1)[-1].lower()
        if provenance == "metropt3_observed" and ref.startswith("metropt3:report:")
        else None
    )
    return out


def upgrade_retrieval_v0(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "retrieval_case/v0":
        raise ValueError(f"not a retrieval_case/v0 record: {record.get('case_id')!r}")
    out = dict(record)
    out["schema_version"] = "retrieval_case/v1"
    out["split"] = "development"
    out["origin"] = {"kind": "authored_team", "derived_from": INHERITED_RETRIEVAL_SOURCE}
    out["cluster_id"] = None
    out["paraphrase_group_id"] = None
    out["content_origin"] = "native"
    out["source_chunk_ids"] = []
    out["source_chunk_sha256"] = {}
    out["relevance_source"] = "inherited_prefix_v0"
    out["annotator_ids"] = []
    return out


def dumps(record: Mapping[str, Any]) -> str:
    return json.dumps(dict(record), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def upgrade_file(src: Path, dst: Path) -> int:
    """Upgrade every record of a v0 case file; returns the record count."""
    rows: list[str] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        task = record.get("task")
        if task == "rca":
            rows.append(dumps(upgrade_rca_v0(record)))
        elif task == "retrieval":
            rows.append(dumps(upgrade_retrieval_v0(record)))
        else:
            raise ValueError(f"unknown task {task!r} in {src.name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    return len(rows)
