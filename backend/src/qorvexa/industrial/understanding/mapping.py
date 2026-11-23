# ruff: noqa: E501
"""Domain mapping: grounded claims -> Production Planning candidates (always untrusted proposals).

Only GROUNDED, asserted (non-negated, non-conditional, non-historical, non-target) claims about
CONFIRMED entities with known units can become SUPPORTED candidates. Everything else stays visible
as NEEDS_CLARIFICATION with the reason. Unsupported features become blocking ``feature:`` proposals.
Nothing here is mathematical validation; the formulation ledger and human confirmation still apply.
"""

from __future__ import annotations

from qorvexa.industrial.formulation import Actor, FormulationError, Ledger
from qorvexa.industrial.governance.authz import Principal, Role
from qorvexa.industrial.understanding.models import (
    Candidate,
    Claim,
    EntityMention,
    Grounding,
    GroundingResult,
    Predicate,
    SemanticType,
    UnsupportedFeature,
)

_MAPPABLE_TYPES = {SemanticType.FACT, SemanticType.ACTUAL_MEASUREMENT}
_RESOURCE_TYPES = {"MACHINE", "RESOURCE", "MATERIAL", "LINE"}
_VALUE_PREDICATES = {Predicate.MARGIN_PER_UNIT, Predicate.COST_PER_UNIT}


def _ent(c: Claim, which: str, ents: dict[str, EntityMention]) -> EntityMention | None:
    ref = c.subject_entity if which == "subject" else c.related_entity
    return ents.get(ref or "")


def _confirmed(e: EntityMention | None, types: set[str]) -> str | None:
    if e is not None and e.status == "CONFIRMED_TERM" and e.canonical_id and e.entity_type in types:
        return e.canonical_id
    return None


def map_to_candidates(
    claims: list[Claim],
    grounding: dict[str, GroundingResult],
    entities: list[EntityMention],
    unsupported: list[UnsupportedFeature],
) -> list[Candidate]:
    ents = {e.mention_id: e for e in entities}
    out: list[Candidate] = []

    def need(c: Claim, key: str, value: str, note: str) -> None:
        out.append(
            Candidate(
                key=key,
                value=value,
                support=(c.claim_id,),
                quote=c.quote,
                status="NEEDS_CLARIFICATION",
                note=note,
            )
        )

    def ok(c: Claim, key: str, value: str) -> None:
        out.append(
            Candidate(
                key=key, value=value, support=(c.claim_id,), quote=c.quote, status="SUPPORTED"
            )
        )

    for c in claims:
        g = grounding[c.claim_id].outcome
        if (
            c.predicate
            not in {
                Predicate.CAPACITY,
                Predicate.DEMAND,
                Predicate.CONSUMPTION_RATE,
                Predicate.PRICE,
                *_VALUE_PREDICATES,
            }
            or c.value is None
        ):
            continue
        if g is not Grounding.GROUNDED:
            continue
        if c.negated or c.semantic_type not in _MAPPABLE_TYPES:
            continue  # targets, history, conditions, hypotheses, questions never become inputs
        v = c.value
        subj, rel = _ent(c, "subject", ents), _ent(c, "related", ents)
        if v.kind == "RANGE":
            need(
                c,
                f"?:{c.predicate.value}",
                f"{v.low}-{v.high}",
                "RANGE_NOT_A_VALUE: choose an exact value or a bound",
            )
            continue
        if v.kind == "PERCENT":
            need(c, f"?:{c.predicate.value}", v.low, "PERCENT_NEEDS_BASE: percentage of what?")
            continue
        if c.predicate is Predicate.PRICE:
            need(
                c,
                f"?:{c.predicate.value}",
                v.low,
                "PRICE_IS_NOT_MARGIN: confirm margin/cost per unit",
            )
            continue
        if c.predicate is Predicate.CAPACITY:
            rid = _confirmed(subj, _RESOURCE_TYPES)
            if rid is None:
                need(c, f"resource:?:capacity({c.subject_text})", v.low, "ENTITY_NOT_CONFIRMED")
            elif v.unit is None or v.per:
                need(
                    c,
                    f"resource:{rid}:capacity",
                    v.low,
                    "MISSING_OR_RATE_UNIT: capacity needs a plain known unit",
                )
            else:
                ok(c, f"resource:{rid}:capacity", v.low)
                ok(c, f"resource:{rid}:unit", v.unit)
        elif c.predicate in _VALUE_PREDICATES:
            pid = _confirmed(subj, {"PRODUCT"})
            if pid is None:
                need(
                    c, f"product:?:value_per_unit({c.subject_text})", v.low, "ENTITY_NOT_CONFIRMED"
                )
            elif v.currency is None or v.unit is None or "/" not in v.unit:
                need(
                    c,
                    f"product:{pid}:value_per_unit",
                    v.low,
                    "MISSING_CURRENCY_OR_UNIT: state e.g. 50 EGP per piece",
                )
            else:
                ok(c, f"product:{pid}:value_per_unit", v.low)
                ok(c, f"product:{pid}:value_unit", v.unit)
                ok(c, "problem:currency", v.currency)
                ok(
                    c,
                    "problem:mode",
                    "MAXIMIZE_MARGIN"
                    if c.predicate is Predicate.MARGIN_PER_UNIT
                    else "MINIMIZE_COST",
                )
        elif c.predicate is Predicate.DEMAND:
            pid = _confirmed(subj, {"PRODUCT"})
            if pid is None:
                need(c, f"product:?:demand({c.subject_text})", v.low, "ENTITY_NOT_CONFIRMED")
            elif c.bound is None:
                need(
                    c,
                    f"product:{pid}:demand",
                    v.low,
                    "DEMAND_SEMANTICS: is it a minimum commitment or a maximum cap?",
                )
            elif v.unit is None:
                need(c, f"product:{pid}:demand", v.low, "MISSING_UNIT")
            else:
                key = "min_quantity" if c.bound == "MIN" else "max_quantity"
                ok(c, f"product:{pid}:{key}", v.low)
                ok(c, f"product:{pid}:bound_unit", v.unit)
        elif c.predicate is Predicate.CONSUMPTION_RATE:
            a, b = subj, rel
            res = _confirmed(a, _RESOURCE_TYPES) or _confirmed(b, _RESOURCE_TYPES)
            prod = _confirmed(a, {"PRODUCT"}) or _confirmed(b, {"PRODUCT"})
            if res is None or prod is None:
                need(
                    c,
                    f"consumption:?:?({c.subject_text},{c.related_text})",
                    v.low,
                    "ENTITY_NOT_CONFIRMED",
                )
            elif v.unit is None or v.per is None:
                need(
                    c,
                    f"consumption:{res}:{prod}",
                    v.low,
                    "MISSING_UNIT: state e.g. 2 hours per piece",
                )
            else:
                ok(c, f"consumption:{res}:{prod}", v.low)
                ok(c, f"consumption:{res}:{prod}:unit", f"{v.unit}/{v.per}")
    for f in unsupported:
        out.append(
            Candidate(
                key=f"feature:{f.feature}", value=f.quote[:500], support=(), quote=f.quote,
                status="BLOCKING_FEATURE",
                note="unmodeled in the continuous-LP slice; blocks formulation until a human decides",
            )
        )  # fmt: skip
    # conflicting duplicates of the same key stay visible; ledger contradiction logic resolves them
    return out


def emit_proposals(
    candidates: list[Candidate], ledger: Ledger, principal: Principal, source_ref: str
) -> tuple[Ledger, list[tuple[Candidate, str]]]:
    """Append SUPPORTED / feature candidates to the claim ledger as UNTRUSTED PROPOSALS.

    Requires a non-human EXTRACTOR service principal; humans must still confirm in the ledger.
    Returns (new ledger, [(candidate, rejection code)]).
    """
    principal.require(Role.EXTRACTOR)
    actor = Actor(id=principal.id, kind=principal.kind)
    out, rejected = ledger, []
    for c in candidates:
        if c.status not in {"SUPPORTED", "BLOCKING_FEATURE"}:
            continue
        try:
            out = out.propose(c.key, c.value, actor, source_ref=source_ref, quote=c.quote[:500])
        except FormulationError as exc:
            rejected.append((c, exc.code))
    return out, rejected
