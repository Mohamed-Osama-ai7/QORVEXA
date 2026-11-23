"""Resolve a ledger into facts, gaps and contradictions; build a problem only when it is settled.

Status priority (worst wins): UNSUPPORTED > CONTRADICTED > NEEDS_CLARIFICATION > INVALID_MODEL >
READY. A READY result is the only one that carries a ``PlanningProblem``; every value in it traces
to a human statement, a human-confirmed proposal, or an allow-listed human-approved assumption.
Nothing is guessed, defaulted or silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from qorvexa.industrial.formulation.ledger import (
    ASSUMABLE,
    NONE_VALUE,
    ActorKind,
    Basis,
    Entry,
    FormulationError,
    Ledger,
    Op,
    parse_key,
)
from qorvexa.industrial.formulation.unitflow import (
    UnitAnalysis,
    active_conversions,
    analyze_units,
    custom_defs,
    fmt_factor,
    is_unit_metadata_key,
)
from qorvexa.industrial.production_planning.compiler import (
    CompiledFormulation,
    canonical_input_json,
    compile_problem,
)
from qorvexa.industrial.production_planning.model import (
    PlanningInputError,
    PlanningProblem,
    validate_planning_dict,
)
from qorvexa.industrial.production_planning.unit_gate import compile_unit_gated
from qorvexa.industrial.units.evidence import LEGACY, VALIDATED, UnitEvidence

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class FormulationStatus(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_MODEL = "INVALID_MODEL"


class FactState(StrEnum):
    CONFIRMED = "CONFIRMED"
    ASSUMED = "ASSUMED"


class ResolvedFact(BaseModel):
    model_config = _FROZEN
    key: str
    value: str
    state: FactState
    claim_seq: int
    basis: str  # USER_STATEMENT | CONFIRMED_PROPOSAL | ASSUMPTION


class Issue(BaseModel):
    model_config = _FROZEN
    code: str  # MISSING_VALUE | CONTRADICTION | UNCONFIRMED_PROPOSAL | UNSUPPORTED_FEATURE | ...
    key: str
    message: str
    question: str = ""
    can_assume: bool = False
    candidates: tuple[str, ...] = ()  # competing / proposed values (never auto-selected)
    claim_seqs: tuple[int, ...] = ()


class FormulationState(BaseModel):
    model_config = _FROZEN
    status: FormulationStatus
    facts: tuple[ResolvedFact, ...]
    issues: tuple[Issue, ...]
    ledger_head: str
    state_sha256: str
    units_status: str = LEGACY  # DIMENSIONALLY_VALIDATED | LEGACY_UNITS_UNVERIFIED | UNITS_BLOCKED
    unit_report_sha256: str = ""


class FormulationRecord(BaseModel):
    """Traceability from the final model back to the ledger (all input → model → decision)."""

    model_config = _FROZEN
    ledger_head: str
    input_sha256: str
    facts: tuple[ResolvedFact, ...]
    assumptions: tuple[tuple[str, str, str, str], ...]  # (key, value, rationale, actor)
    record_sha256: str
    units_status: str = LEGACY
    unit_report_sha256: str = ""
    # (slot, from_unit, to_unit, exact factor, actor, rationale, timestamp)
    conversions: tuple[tuple[str, str, str, str, str, str, str], ...] = ()
    custom_units: tuple[tuple[str, str, str, str], ...] = ()  # (symbol, expr, factor, actor)
    unit_claims: tuple[tuple[str, int, str], ...] = ()  # (key, claim seq, basis) lineage


class FormulationResult(BaseModel):
    model_config = _FROZEN
    state: FormulationState
    problem: PlanningProblem | None = None
    compiled: CompiledFormulation | None = None
    record: FormulationRecord | None = None
    unit_evidence: UnitEvidence | None = None


_QUESTIONS = {
    "id": "What identifier should this planning problem have?",
    "title": "What is the title of this planning problem?",
    "mode": "Is the goal MAXIMIZE_MARGIN (maximize profit) or MINIMIZE_COST "
    "(meet commitments at lowest cost)?",
    "currency": "Which currency are the product values stated in?",
    "name": "What is the display name of {kind} {id}?",
    "unit": "Which unit is {kind} {id} measured in?",
    "value_per_unit": "What is the margin (cost, in cost mode) per unit of product {id}?",
    "min_quantity": "Must product {id} be produced in at least some quantity (a commitment)?",
    "max_quantity": "Is production of product {id} capped (demand or market limit)?",
    "kind": "What kind of resource is {id} (MACHINE, LABOR, MATERIAL, ENERGY, OTHER)?",
    "capacity": "How much of resource {id} is available in the planning period?",
    "value_unit": "In which unit is the value of product {id} stated (e.g. EUR/piece)?",
    "bound_unit": "In which unit are the min/max quantities of product {id} stated?",
}


def _q(field: str, kind: str, ident: str) -> str:
    return _QUESTIONS[field].format(kind=kind, id=ident)


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _group(ledger: Ledger) -> dict[str, list[Entry]]:
    groups: dict[str, list[Entry]] = {}
    for e in ledger.entries:
        if e.op in (Op.ASSERT, Op.ASSUME) and e.key and ledger.is_active(e.seq):
            groups.setdefault(e.key, []).append(e)
    return groups


def _resolve_key(
    key: str, group: list[Entry], confirmed: frozenset[int]
) -> tuple[ResolvedFact | None, Issue | None]:
    auth = [
        e
        for e in group
        if e.op is Op.ASSERT and (e.basis is Basis.USER_STATEMENT or e.seq in confirmed)
    ]
    assumed = [e for e in group if e.op is Op.ASSUME]
    proposals = [e for e in group if e.basis is Basis.PROPOSAL and e.seq not in confirmed]

    def contradiction(entries: list[Entry]) -> Issue:
        values = tuple(sorted({e.value or "" for e in entries}))
        return Issue(
            code="CONTRADICTION",
            key=key,
            message=f"{key} has conflicting values {list(values)}; a human must resolve it",
            question=f"Which value is correct for {key}: {', '.join(values)}?",
            candidates=values,
            claim_seqs=tuple(e.seq for e in entries),
        )

    if auth:
        if len({e.value for e in auth}) > 1:
            return None, contradiction(auth)
        win = auth[-1]
        basis = "USER_STATEMENT" if win.basis is Basis.USER_STATEMENT else "CONFIRMED_PROPOSAL"
        return ResolvedFact(
            key=key,
            value=win.value or "",
            state=FactState.CONFIRMED,
            claim_seq=win.seq,
            basis=basis,
        ), None
    if assumed:
        if len({e.value for e in assumed}) > 1:
            return None, contradiction(assumed)
        win = assumed[-1]
        return ResolvedFact(
            key=key,
            value=win.value or "",
            state=FactState.ASSUMED,
            claim_seq=win.seq,
            basis="ASSUMPTION",
        ), None
    if proposals:
        values = tuple(sorted({e.value or "" for e in proposals}))
        return None, Issue(
            code="UNCONFIRMED_PROPOSAL",
            key=key,
            message=f"{key} is only proposed ({list(values)}); a human must confirm or replace it",
            question=f"Is {key} = {' or '.join(values)} correct?",
            candidates=values,
            claim_seqs=tuple(e.seq for e in proposals),
        )
    return None, None


def _missing(key: str, field: str, kind: str, ident: str, can_assume: bool) -> Issue:
    return Issue(
        code="MISSING_VALUE",
        key=key,
        message=f"{key} is unknown and has not been stated",
        question=_q(field, kind, ident),
        can_assume=can_assume,
    )


_ORDER = [
    FormulationStatus.UNSUPPORTED,
    FormulationStatus.CONTRADICTED,
    FormulationStatus.NEEDS_CLARIFICATION,
    FormulationStatus.INVALID_MODEL,
]


def _status(issues: list[Issue]) -> FormulationStatus:
    codes = {i.code for i in issues}
    if codes & {"UNSUPPORTED_FEATURE", "UNMODELED_CONSIDERATION"}:
        return FormulationStatus.UNSUPPORTED
    if "CONTRADICTION" in codes:
        return FormulationStatus.CONTRADICTED
    if codes & {
        "MISSING_VALUE",
        "UNCONFIRMED_PROPOSAL",
        "UNDECLARED_REFERENCE",
        "CONVERSION_REQUIRED",
        "UNKNOWN_UNIT",
        "AMBIGUOUS_UNIT",
        "OFFSET_UNIT_UNSUPPORTED",
        "PARSE_ERROR",
    }:
        return FormulationStatus.NEEDS_CLARIFICATION
    if codes:
        return FormulationStatus.INVALID_MODEL
    return FormulationStatus.READY


def resolve_facts(ledger: Ledger) -> tuple[dict[str, ResolvedFact], list[Issue]]:
    """Resolve claims to facts (no ledger verification; callers verify)."""
    from qorvexa.industrial.production_planning.model import UNSUPPORTED_FEATURE_KEYS

    confirmed = ledger.confirmed()
    groups = _group(ledger)
    facts: dict[str, ResolvedFact] = {}
    issues: list[Issue] = []

    for key in sorted(groups):
        pk = parse_key(key)
        if pk.scope == "feature":
            known = pk.a in UNSUPPORTED_FEATURE_KEYS
            seqs = tuple(e.seq for e in groups[key])
            issues.append(
                Issue(
                    code="UNSUPPORTED_FEATURE" if known else "UNMODELED_CONSIDERATION",
                    key=key,
                    message=(
                        f"{pk.a}: {UNSUPPORTED_FEATURE_KEYS[pk.a]} are not supported by the "
                        "continuous LP slice"
                        if known
                        else f"{pk.a} was stated but cannot be modeled; it would be silently "
                        "ignored"
                    ),
                    question="Retract this consideration (acknowledging it is out of scope) "
                    "or stop and choose a different formulation.",
                    claim_seqs=seqs,
                )
            )
            continue
        fact, issue = _resolve_key(key, groups[key], confirmed)
        if fact:
            facts[key] = fact
        if issue:
            issues.append(issue)
    return facts, issues


def analyze(ledger: Ledger) -> FormulationState:
    """Pure, deterministic analysis. Raises FormulationError(LEDGER_TAMPERED) on a forged ledger."""
    return _analyze(ledger)[0]


def _analyze(ledger: Ledger) -> tuple[FormulationState, UnitAnalysis | None]:
    ledger.verify()
    groups = _group(ledger)
    facts, issues = resolve_facts(ledger)

    def has(key: str) -> bool:
        return key in facts

    def need(key: str, field: str, kind: str, ident: str, can_assume: bool = False) -> None:
        if not has(key) and not any(i.key == key for i in issues):
            issues.append(_missing(key, field, kind, ident, can_assume))

    for f in ("id", "title", "mode", "currency"):
        need(f"problem:{f}", f, "problem", "")
    products = sorted({parse_key(k).a for k in groups if parse_key(k).scope == "product"})
    resources = sorted({parse_key(k).a for k in groups if parse_key(k).scope == "resource"})
    if not products:
        issues.append(
            Issue(
                code="MISSING_VALUE",
                key="product",
                message="no product has been declared",
                question="Which products can be produced?",
            )
        )
    if not resources:
        issues.append(
            Issue(
                code="MISSING_VALUE",
                key="resource",
                message="no resource has been declared",
                question="Which resources limit production?",
            )
        )
    for p in products:
        for f in ("name", "unit", "value_per_unit"):
            need(f"product:{p}:{f}", f, "product", p)
        need(f"product:{p}:min_quantity", "min_quantity", "product", p, True)
        need(f"product:{p}:max_quantity", "max_quantity", "product", p, True)
    for r in resources:
        for f in ("name", "kind", "unit", "capacity"):
            need(f"resource:{r}:{f}", f, "resource", r)
    strict = any(is_unit_metadata_key(k) for k in groups)
    if strict:
        for p in products:
            need(f"product:{p}:value_unit", "value_unit", "product", p)
            lo, hi = facts.get(f"product:{p}:min_quantity"), facts.get(f"product:{p}:max_quantity")
            if (lo is not None and lo.value != "0") or (hi is not None and hi.value != NONE_VALUE):
                need(f"product:{p}:bound_unit", "bound_unit", "product", p)
        for r in resources:
            for p in products:
                ukey = f"consumption:{r}:{p}:unit"
                if not has(ukey) and not any(i.key == ukey for i in issues):
                    issues.append(
                        Issue(
                            code="MISSING_VALUE",
                            key=ukey,
                            message=f"{ukey} is unknown; units are never assumed",
                            question=f"In which unit is the consumption of {r} per unit of {p} "
                            "stated (e.g. h/piece)?",
                        )
                    )
    for k in sorted(groups):
        pk = parse_key(k)
        if pk.scope == "consumption" and (pk.a not in resources or pk.b not in products):
            issues.append(
                Issue(
                    code="UNDECLARED_REFERENCE",
                    key=k,
                    message=f"{k} refers to an undeclared resource or product "
                    f"(declared resources {resources}, products {products})",
                    question="Is this a typo, or must the resource/product be declared first?",
                    claim_seqs=tuple(e.seq for e in groups[k]),
                )
            )
    for r in resources:
        for p in products:
            key = f"consumption:{r}:{p}"
            if not any(i.key == key for i in issues) and not has(key):
                issues.append(
                    Issue(
                        code="MISSING_VALUE",
                        key=key,
                        message=f"how much of {r} one unit of {p} uses is unknown",
                        question=f"How much of resource {r} does one unit of product {p} consume "
                        "(0 if it does not use it)?",
                        can_assume=True,
                    )
                )
    ua: UnitAnalysis | None = None
    units_status = LEGACY
    unit_sha = ""
    if strict:
        units_status = "UNITS_BLOCKED"
        if not issues:
            ua = analyze_units(ledger, facts, products, resources)
            issues.extend(
                Issue(
                    code=u.code,
                    key=u.key,
                    message=u.message,
                    question=u.question,
                    candidates=u.candidates,
                )
                for u in ua.issues
            )
            if ua.evidence is not None:
                units_status, unit_sha = VALIDATED, ua.evidence.report_sha256
    issues.sort(key=lambda i: (i.code, i.key))
    ordered = tuple(facts[k] for k in sorted(facts))
    status = _status(issues)
    body: dict[str, Any] = {
        "status": status.value,
        "facts": [f.model_dump() for f in ordered],
        "issues": [i.model_dump() for i in issues],
        "ledger_head": ledger.head,
    }
    if strict:  # legacy ledgers keep their original state hash
        body["units"] = [units_status, unit_sha]
    state = FormulationState(
        status=status,
        facts=ordered,
        issues=tuple(issues),
        ledger_head=ledger.head,
        state_sha256=_digest(body),
        units_status=units_status,
        unit_report_sha256=unit_sha,
    )
    return state, ua


def _assemble(
    facts: dict[str, ResolvedFact],
    eff_values: dict[str, str] | None = None,
    runits: dict[str, str] | None = None,
) -> dict[str, Any]:
    over = eff_values or {}
    ru = runits or {}

    def v(key: str) -> str:
        return over.get(key, facts[key].value)

    products: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    cons: list[dict[str, Any]] = []
    pids = sorted({parse_key(k).a for k in facts if parse_key(k).scope == "product"})
    rids = sorted({parse_key(k).a for k in facts if parse_key(k).scope == "resource"})
    currency = v("problem:currency")
    for p in pids:
        mx = v(f"product:{p}:max_quantity")
        products.append(
            {
                "id": p,
                "name": v(f"product:{p}:name"),
                "unit": v(f"product:{p}:unit"),
                "value_per_unit": v(f"product:{p}:value_per_unit"),
                "value_currency": currency,
                "min_quantity": v(f"product:{p}:min_quantity"),
                "max_quantity": None if mx == NONE_VALUE else mx,
            }
        )
    for r in rids:
        resources.append(
            {
                "id": r,
                "name": v(f"resource:{r}:name"),
                "kind": v(f"resource:{r}:kind"),
                "unit": ru.get(r, v(f"resource:{r}:unit")),
                "capacity": v(f"resource:{r}:capacity"),
            }
        )
    runit = {r["id"]: r["unit"] for r in resources}
    punit = {p["id"]: p["unit"] for p in products}
    for r in rids:
        for p in pids:
            cons.append(
                {
                    "resource_id": r,
                    "product_id": p,
                    "amount_per_unit": v(f"consumption:{r}:{p}"),
                    "resource_unit": runit[r],
                    "product_unit": punit[p],
                }
            )
    meta: dict[str, Any] = {"problem_id": v("problem:id"), "title": v("problem:title")}
    if "problem:period_label" in facts:
        meta["period_label"] = v("problem:period_label")
    return {
        "metadata": meta,
        "mode": v("problem:mode"),
        "currency": currency,
        "products": products,
        "resources": resources,
        "consumptions": cons,
    }


def formulate(ledger: Ledger) -> FormulationResult:
    """Analyze; when READY build, validate and compile. Never returns a model with open issues."""
    state, ua = _analyze(ledger)
    if state.status is not FormulationStatus.READY:
        return FormulationResult(state=state)
    facts = {f.key: f for f in state.facts}
    eff = ua.eff if ua is not None else None
    runits = (
        {k.split(":", 1)[1]: u for k, u in eff.units.items() if k.startswith("resource:")}
        if eff
        else None
    )
    try:
        problem = validate_planning_dict(_assemble(facts, eff.values if eff else None, runits))
    except PlanningInputError as exc:
        issue = Issue(
            code=exc.code,
            key="problem",
            message=exc.message,
            question="Correct or clarify the stated facts so the model validates.",
        )
        bad = state.model_copy(
            update={"status": FormulationStatus.INVALID_MODEL, "issues": (issue,)}
        )
        body = bad.model_dump(mode="json", exclude={"state_sha256"})
        return FormulationResult(state=bad.model_copy(update={"state_sha256": _digest(body)}))
    input_sha = hashlib.sha256(canonical_input_json(problem).encode("utf-8")).hexdigest()
    assumptions = tuple(
        (f.key, f.value, ledger.get(f.claim_seq).rationale, ledger.get(f.claim_seq).actor.id)
        for f in state.facts
        if f.state is FactState.ASSUMED
    )
    rec_body: dict[str, Any] = {
        "ledger_head": ledger.head,
        "input_sha256": input_sha,
        "facts": [f.model_dump() for f in state.facts],
        "assumptions": [list(a) for a in assumptions],
    }
    conversions: tuple[tuple[str, str, str, str, str, str, str], ...] = ()
    custom: tuple[tuple[str, str, str, str], ...] = ()
    claims: tuple[tuple[str, int, str], ...] = ()
    evidence: UnitEvidence | None = ua.evidence if ua is not None else None
    if evidence is not None:
        conversions = tuple(
            (e.key or "", e.unit_from, e.value or "", e.factor, e.actor.id, e.rationale, e.at)
            for e in active_conversions(ledger)
        )
        by_key = {f.key: f for f in state.facts}
        custom = tuple(
            (
                d.symbol,
                d.expression,
                fmt_factor(d.factor),
                ledger.get(by_key[f"unitdef:{d.symbol}"].claim_seq).actor.id,
            )
            for d in custom_defs({f.key: f.value for f in state.facts})
        )
        claims = tuple(
            (f.key, f.claim_seq, f.basis)
            for f in state.facts
            if is_unit_metadata_key(f.key)
            or f.key.endswith(":unit")
            or f.key.startswith("unitdef:")
        )
        rec_body["units"] = {
            "status": state.units_status,
            "report_sha256": evidence.report_sha256,
            "conversions": [list(c) for c in conversions],
            "custom_units": [list(c) for c in custom],
            "claims": [list(c) for c in claims],
        }
    record = FormulationRecord(
        ledger_head=ledger.head,
        input_sha256=input_sha,
        facts=state.facts,
        assumptions=assumptions,
        record_sha256=_digest(rec_body),
        units_status=state.units_status,
        unit_report_sha256=state.unit_report_sha256,
        conversions=conversions,
        custom_units=custom,
        unit_claims=claims,
    )
    lineage = (f"formulation_ledger:{ledger.head}", f"formulation_record:{record.record_sha256}")
    if evidence is not None:
        try:  # defense in depth: the gate re-verifies the evidence independently
            compiled = compile_unit_gated(problem, evidence, lineage=lineage)
        except PlanningInputError as exc:
            issue = Issue(code=exc.code, key="units", message=exc.message)
            bad = state.model_copy(
                update={"status": FormulationStatus.INVALID_MODEL, "issues": (issue,)}
            )
            return FormulationResult(state=bad)
    else:
        compiled = compile_problem(problem, lineage=lineage)
    return FormulationResult(
        state=state, problem=problem, compiled=compiled, record=record, unit_evidence=evidence
    )


def apply_defaults(ledger: Ledger, actor_id: str, rationale: str) -> Ledger:
    """A human accepts *every* currently-assumable gap in one explicit, recorded act."""
    state = analyze(ledger)
    out = ledger
    for i in state.issues:
        if i.code == "MISSING_VALUE" and i.can_assume:
            pk = parse_key(i.key)
            out = out.assume(i.key, ASSUMABLE[(pk.scope, pk.field)], actor_id, rationale)
    return out


def confirm_keys(ledger: Ledger, keys: list[str], actor_id: str) -> Ledger:
    """A human confirms the latest active unconfirmed proposal for each named key."""
    out = ledger
    for key in keys:
        cands = [
            e
            for e in out.entries
            if e.op is Op.ASSERT
            and e.basis is Basis.PROPOSAL
            and e.key == key
            and out.is_active(e.seq)
            and e.seq not in out.confirmed()
        ]
        if not cands:
            raise FormulationError("NOT_A_PROPOSAL", f"no unconfirmed proposal for {key}")
        out = out.confirm(cands[-1].seq, actor_id)
    return out


__all__ = [
    "ActorKind",
    "FactState",
    "FormulationRecord",
    "FormulationResult",
    "FormulationState",
    "FormulationStatus",
    "Issue",
    "ResolvedFact",
    "analyze",
    "apply_defaults",
    "confirm_keys",
    "formulate",
]
