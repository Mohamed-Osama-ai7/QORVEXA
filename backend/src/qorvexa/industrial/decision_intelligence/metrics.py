"""Deterministic metrics derived only from a verified ProductionReport."""

from __future__ import annotations

from decimal import Decimal

from qorvexa.industrial.decision_intelligence.numeric import CTX, REL_TOL, ZERO
from qorvexa.industrial.production_planning.report import ProductionReport, ReportStatus


def shortfall(quantity: Decimal, minimum: Decimal) -> Decimal:
    gap = minimum - quantity
    if gap <= REL_TOL * max(Decimal(1), abs(minimum)):
        return ZERO
    return gap


def report_metrics(report: ProductionReport) -> dict[str, Decimal]:
    """Metric key -> value. Empty for non-solved reports (unavailable, never zero)."""
    if report.status != ReportStatus.VERIFIED_NUMERICAL_SOLUTION or report.objective_value is None:
        return {}
    m: dict[str, Decimal] = {"objective": report.objective_value}
    for q in report.quantities:
        m[f"quantity:{q.product_id}"] = q.quantity
    units = {q.unit for q in report.quantities}
    if len(units) == 1:  # a total over mixed product units would be meaningless
        m["total_volume"] = sum((q.quantity for q in report.quantities), ZERO)
    utils: list[Decimal] = []
    for u in report.utilization:
        m[f"used:{u.resource_id}"] = u.used
        m[f"remaining:{u.resource_id}"] = u.remaining
        if u.utilization is not None:
            m[f"utilization:{u.resource_id}"] = u.utilization
            utils.append(u.utilization)
    if utils:
        m["mean_utilization"] = CTX.divide(sum(utils, ZERO), Decimal(len(utils)))
        m["max_utilization"] = max(utils)
    m["bottleneck_count"] = Decimal(len(report.bottleneck_resource_ids))
    total_short = ZERO
    for d in report.demand:
        s = shortfall(d.quantity, d.minimum)
        m[f"shortfall:{d.product_id}"] = s
        total_short += s
    m["total_shortfall"] = total_short
    return m
