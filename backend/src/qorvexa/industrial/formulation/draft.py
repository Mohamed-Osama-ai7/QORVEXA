"""Turn a partial, untrusted draft (e.g. an LLM extraction) into PROPOSAL claims only.

The draft shape mirrors the planning input but every field is optional. Nothing here can create a
trusted fact: proposals stay unconfirmed until a human confirms them. The whole draft is applied
atomically; one invalid value rejects it. Unknown keys are refused, never ignored.
"""

from __future__ import annotations

from typing import Any

from qorvexa.industrial.formulation.ledger import (
    PRODUCT_FIELDS,
    RESOURCE_FIELDS,
    Actor,
    FormulationError,
    Ledger,
)
from qorvexa.industrial.production_planning.model import UNSUPPORTED_FEATURE_KEYS

_TOP = {"problem", "products", "resources", "consumptions", "features", "unit_definitions"}


def _only(d: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(d, dict):
        raise FormulationError("INVALID_DRAFT", f"{where} must be an object")
    extra = sorted(set(d) - allowed)
    if extra:
        if extra[0] in UNSUPPORTED_FEATURE_KEYS:
            raise FormulationError(
                "UNSUPPORTED_FEATURE",
                f"{where}.{extra[0]}: {UNSUPPORTED_FEATURE_KEYS[extra[0]]} are not supported; "
                "record it under 'features' to make the gap visible",
            )
        raise FormulationError("INVALID_DRAFT", f"{where}: unknown field(s) {extra}")
    return d


def propose_from_draft(
    ledger: Ledger, draft: dict[str, Any], actor: Actor, *, source_ref: str = ""
) -> Ledger:
    """Append PROPOSAL claims for every field present in ``draft`` (atomic)."""
    _only(draft, _TOP, "draft")
    out = ledger
    problem = _only(
        draft.get("problem", {}), {"id", "title", "mode", "currency", "period_label"}, "problem"
    )
    for f, v in problem.items():
        out = out.propose(f"problem:{f}", v, actor, source_ref=source_ref)
    for i, p in enumerate(draft.get("products", ())):
        row = _only(p, {"id", *PRODUCT_FIELDS}, f"products[{i}]")
        if "id" not in row:
            raise FormulationError("INVALID_DRAFT", f"products[{i}] has no id")
        for f in PRODUCT_FIELDS:
            if f in row:
                out = out.propose(f"product:{row['id']}:{f}", row[f], actor, source_ref=source_ref)
    for i, r in enumerate(draft.get("resources", ())):
        row = _only(r, {"id", *RESOURCE_FIELDS}, f"resources[{i}]")
        if "id" not in row:
            raise FormulationError("INVALID_DRAFT", f"resources[{i}] has no id")
        for f in RESOURCE_FIELDS:
            if f in row:
                out = out.propose(f"resource:{row['id']}:{f}", row[f], actor, source_ref=source_ref)
    for i, c in enumerate(draft.get("consumptions", ())):
        row = _only(
            c, {"resource_id", "product_id", "amount_per_unit", "unit"}, f"consumptions[{i}]"
        )
        if not {"resource_id", "product_id", "amount_per_unit"} <= set(row):
            raise FormulationError("INVALID_DRAFT", f"consumptions[{i}] is incomplete")
        out = out.propose(
            f"consumption:{row['resource_id']}:{row['product_id']}",
            row["amount_per_unit"],
            actor,
            source_ref=source_ref,
        )
        if "unit" in row:
            out = out.propose(
                f"consumption:{row['resource_id']}:{row['product_id']}:unit",
                row["unit"],
                actor,
                source_ref=source_ref,
            )
    for i, u in enumerate(draft.get("unit_definitions", ())):
        row = _only(u, {"symbol", "factor", "expression"}, f"unit_definitions[{i}]")
        if set(row) != {"symbol", "factor", "expression"}:
            raise FormulationError("INVALID_DRAFT", f"unit_definitions[{i}] is incomplete")
        out = out.propose(
            f"unitdef:{row['symbol']}",
            f"{row['factor']}|{row['expression']}",
            actor,
            source_ref=source_ref,
        )
    for i, f in enumerate(draft.get("features", ())):
        row = _only(f, {"name", "description"}, f"features[{i}]")
        if set(row) != {"name", "description"}:
            raise FormulationError("INVALID_DRAFT", f"features[{i}] needs name and description")
        out = out.propose(
            f"feature:{row['name']}", row["description"], actor, source_ref=source_ref
        )
    return out
