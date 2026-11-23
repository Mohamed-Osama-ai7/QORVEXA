"""Deterministic trade-off records derived only from verified comparison numbers.

No business cost, probability or market effect is invented. Anything that would need such data is
reported as UNAVAILABLE with the reason.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from qorvexa.industrial.decision_intelligence.comparison import Effect, ScenarioComparison
from qorvexa.industrial.decision_intelligence.models import F
from qorvexa.industrial.decision_intelligence.numeric import CTX, close, fmt
from qorvexa.industrial.production_planning.model import ObjectiveMode

_STD_ASSUMPTIONS = (
    "Scenario inputs are the user's declared values; they are not validated against a factory.",
    "Only the modeled resources, bounds and objective are considered.",
)
_STD_LIMITS = (
    "GAP_NUMERICAL verification is numerical, not an exact optimality proof.",
    "Observed difference between two independently solved scenarios; not a sensitivity or "
    "shadow-price statement.",
)


class TradeOff(BaseModel):
    model_config = F
    trade_off_id: str
    scenario_id: str
    state: Literal["AVAILABLE", "UNAVAILABLE"]
    improved_metric: str
    affected_metric: str
    magnitude: Decimal | None
    direction: str
    adverse: bool
    summary: str
    evidence: tuple[str, ...]
    assumptions: tuple[str, ...] = _STD_ASSUMPTIONS
    limitations: tuple[str, ...] = _STD_LIMITS


def _t(
    tid: str,
    c: ScenarioComparison,
    improved: str,
    affected: str,
    magnitude: Decimal | None,
    direction: str,
    adverse: bool,
    summary: str,
    evidence: list[str],
    extra_assumptions: tuple[str, ...] = (),
) -> TradeOff:
    return TradeOff(
        trade_off_id=tid,
        scenario_id=c.scenario_id,
        state="AVAILABLE",
        improved_metric=improved,
        affected_metric=affected,
        magnitude=magnitude,
        direction=direction,
        adverse=adverse,
        summary=summary,
        evidence=tuple(evidence),
        assumptions=_STD_ASSUMPTIONS + extra_assumptions,
    )


def analyze(c: ScenarioComparison, mode: ObjectiveMode) -> tuple[TradeOff, ...]:
    out: list[TradeOff] = []
    if c.objective is None:
        if c.status == "INFEASIBLE_VERIFIED":
            kinds = sorted({ch.kind for ch in c.input_diff.changes})
            out.append(
                _t(
                    "FEASIBILITY_LOST",
                    c,
                    "input change",
                    "feasibility",
                    None,
                    "LOST",
                    True,
                    f"Scenario {c.scenario_id} is verified infeasible (changed inputs: "
                    f"{', '.join(kinds) or 'none'}); no production plan satisfies it.",
                    [f"status {c.status}", f"baseline {c.baseline_status}"],
                )
            )
        else:
            out.append(
                TradeOff(
                    trade_off_id="UNAVAILABLE_NO_SOLUTION",
                    scenario_id=c.scenario_id,
                    state="UNAVAILABLE",
                    improved_metric="n/a",
                    affected_metric="n/a",
                    magnitude=None,
                    direction="UNKNOWN",
                    adverse=False,
                    summary=f"No verified solution for status {c.status}; trade-offs unavailable.",
                    evidence=(f"status {c.status}",),
                )
            )
        return tuple(out)

    obj = c.objective
    improved = obj.effect == Effect.IMPROVED
    worse = obj.effect == Effect.DETERIORATED
    obj_txt = f"objective {fmt(obj.baseline)} -> {fmt(obj.scenario)} {obj.currency}"
    newly = [r for r in c.resources if r.binding_change == "BECAME_BINDING"]
    if improved and newly:
        ids = ", ".join(r.resource_id for r in newly)
        out.append(
            _t(
                "OBJECTIVE_VS_NEW_BOTTLENECKS",
                c,
                "objective",
                "binding resources",
                Decimal(len(newly)),
                "MORE_RESOURCES_AT_CAPACITY",
                True,
                f"Objective improves but {ids} become(s) fully used.",
                [obj_txt]
                + [f"{r.resource_id} remaining {fmt(r.remaining_scenario)}" for r in newly],
            )
        )
    shrunk = [
        r
        for r in c.resources
        if r.capacity_baseline == r.capacity_scenario
        and not close(r.remaining_baseline, r.remaining_scenario)
        and r.remaining_scenario < r.remaining_baseline
    ]
    if improved and shrunk:
        out.append(
            _t(
                "OBJECTIVE_VS_REMAINING_CAPACITY",
                c,
                "objective",
                "remaining capacity",
                sum((r.remaining_baseline - r.remaining_scenario for r in shrunk), Decimal(0)),
                "LESS_SLACK_ON_UNCHANGED_RESOURCES",
                True,
                "Objective improves while slack shrinks on unchanged resources: "
                + ", ".join(
                    f"{r.resource_id} {fmt(r.remaining_baseline)}->{fmt(r.remaining_scenario)}"
                    f" {r.unit}"
                    for r in shrunk
                ),
                [obj_txt],
            )
        )
    utils = [r.utilization_change for r in c.resources if r.utilization_change is not None]
    if utils:
        mean_delta = CTX.divide(sum(utils, Decimal(0)), Decimal(len(utils)))
        if not close(mean_delta, Decimal(0)):
            more = mean_delta > 0
            if (improved and more) or (worse and more):
                out.append(
                    _t(
                        "OBJECTIVE_VS_UTILIZATION",
                        c,
                        "objective" if improved else "resource use",
                        "mean utilization" if improved else "objective",
                        mean_delta,
                        "HIGHER_MEAN_UTILIZATION",
                        worse,
                        (
                            "Higher objective with higher mean utilization."
                            if improved
                            else "More resource use for a lower objective."
                        ),
                        [obj_txt, f"mean utilization change {fmt(mean_delta)}"],
                    )
                )
    bq = sum((p.baseline for p in c.products), Decimal(0))
    sq = sum((p.scenario for p in c.products), Decimal(0))
    units = {p.unit for p in c.products}
    if len(units) == 1 and not close(bq, sq):
        dv = sq - bq
        if mode is ObjectiveMode.MINIMIZE_COST and improved and dv < 0:
            out.append(
                _t(
                    "COST_VS_VOLUME",
                    c,
                    "variable cost",
                    "production volume",
                    dv,
                    "LOWER_VOLUME",
                    True,
                    f"Cost falls but total production falls by {fmt(-dv)} {next(iter(units))}.",
                    [obj_txt],
                )
            )
        if mode is ObjectiveMode.MAXIMIZE_MARGIN and improved and dv < 0:
            out.append(
                _t(
                    "MIX_SHIFT_TO_HIGHER_VALUE",
                    c,
                    "margin",
                    "production volume",
                    dv,
                    "LOWER_VOLUME",
                    False,
                    f"Margin improves with {fmt(-dv)} {next(iter(units))} less total volume "
                    "(mix shift).",
                    [obj_txt],
                )
            )
        if mode is ObjectiveMode.MAXIMIZE_MARGIN and worse and dv > 0:
            out.append(
                _t(
                    "VOLUME_VS_MARGIN",
                    c,
                    "production volume",
                    "margin",
                    dv,
                    "HIGHER_VOLUME_LOWER_MARGIN",
                    True,
                    f"Total volume rises by {fmt(dv)} but margin falls.",
                    [obj_txt],
                )
            )
    caps = c.input_diff.of_kind("capacity")
    if len(c.input_diff.changes) == 1 and len(caps) == 1:
        ch = caps[0]
        if ch.old is not None and ch.new is not None and ch.new != ch.old:
            dcap = ch.new - ch.old
            per_unit = CTX.divide(obj.absolute_change, dcap)
            out.append(
                _t(
                    "CAPACITY_CHANGE_VS_OBJECTIVE",
                    c,
                    "objective",
                    f"capacity of {ch.target_id}",
                    per_unit,
                    "OBSERVED_OBJECTIVE_CHANGE_PER_CAPACITY_UNIT",
                    False,
                    f"Changing {ch.target_id} capacity by {fmt(dcap)} moved the objective by "
                    f"{fmt(obj.absolute_change)} {obj.currency} (average {fmt(per_unit)} per unit "
                    "between these two solved scenarios; not a shadow price).",
                    [obj_txt, f"capacity {fmt(ch.old)} -> {fmt(ch.new)}"],
                    ("The cost of changing this capacity is not modeled.",),
                )
            )
    raised = [d for d in c.demand if d.minimum_scenario > d.minimum_baseline]
    if raised:
        out.append(
            _t(
                "DEMAND_COMMITMENT_VS_OBJECTIVE",
                c,
                "demand commitment",
                "objective",
                obj.absolute_change,
                f"OBJECTIVE_{obj.effect.value}",
                worse,
                "Higher minimum commitments on "
                + ", ".join(d.product_id for d in raised)
                + f" with {obj_txt}; scenario remains feasible.",
                [obj_txt],
            )
        )
    return tuple(out)
