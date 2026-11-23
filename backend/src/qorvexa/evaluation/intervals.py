"""Confidence intervals and conservative variants for run summaries.

This is the *policy* layer over ``stats.py``. It decides, from what the cases declare, whether
observations are independent, and never lets an unknown dependence structure fall through to an
item-level bootstrap:

* every scored case has a ``cluster_id`` and each cluster holds one case -> treated as
  independent (``stats.compute_metric_interval(..., independent=True)``);
* some cluster holds several cases -> ``stats`` cluster bootstrap on the declared clusters;
* any scored case lacks a ``cluster_id`` -> the interval is ``unavailable`` with that reason.

Intervals measure benchmark **sampling variability only** (how much the metric would move with a
different sample of similar cases). They are not statements about factory generalisation, other
assets, other documents or other languages, and they ignore labelling and selection bias.

The conservative variant counts ``undecided`` outcomes as failures. It exists only for the
explicitly binary metrics in ``BINARY_METRICS``; the measured metric is never changed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from typing import Any

from qorvexa.evaluation.schemas import CaseResult
from qorvexa.evaluation.stats import ConfidenceInterval, compute_metric_interval

BASE_SEED = 20261117
RESAMPLES = 2000

# Metrics whose per-case value is explicitly 0 or 1.
BINARY_METRICS: frozenset[str] = frozenset(
    {
        "category_match",
        "unacceptable_hypothesis",
        "abstention_correct",
        "critical_contradiction_disclosure",
        "safe_diagnostic_disposition",
        "critical_contradiction_handling",
        "hit_rate_at_5",
        "out_of_scope_abstention",
    }
)

SAMPLING_NOTE = (
    "Intervals measure benchmark sampling variability only; they do not describe factory "
    "generalisation, other assets, documents or languages, nor labelling or selection bias."
)


def derive_seed(metric: str, base: int = BASE_SEED) -> int:
    """Stable per-metric seed (fixed base + metric name), so output is deterministic."""
    return int.from_bytes(hashlib.sha256(f"{base}:{metric}".encode()).digest()[:8], "big")


def _unavailable(n: int, point: float | None, reason: str) -> ConfidenceInterval:
    return ConfidenceInterval(
        point_estimate=point,
        lower=None,
        upper=None,
        method="none",
        state="unavailable",
        n_samples=n,
        reason=reason,
    )


def metric_interval(
    metric: str, results: Sequence[CaseResult], *, resamples: int = RESAMPLES
) -> ConfidenceInterval:
    """Interval for the mean of ``metric`` over the ``scored`` outcomes of ``results``."""
    scored = [
        (r.metrics[metric].value, r.cluster_id)
        for r in results
        if r.metrics[metric].state == "scored" and r.metrics[metric].value is not None
    ]
    values = [float(v) for v, _ in scored if v is not None]
    n = len(values)
    point = None if n == 0 else sum(values) / n
    if n == 0:
        return _unavailable(0, None, "no scored observations")
    clusters = [c for _, c in scored]
    if any(c is None for c in clusters):
        return _unavailable(
            n,
            point,
            "dependence structure unknown: some cases lack cluster_id and independence is not "
            "assumed",
        )
    known = [str(c) for c in clusters]
    seed = derive_seed(metric)
    if all(count == 1 for count in Counter(known).values()):
        return compute_metric_interval(values, independent=True, n_resamples=resamples, seed=seed)
    return compute_metric_interval(values, cluster_ids=known, n_resamples=resamples, seed=seed)


def conservative_binary(metric: str, results: Sequence[CaseResult]) -> dict[str, Any] | None:
    """Undecided-as-failure variant for an explicitly binary metric; ``None`` otherwise.

    The measured rate (``scored`` outcomes only) is reported unchanged beside the conservative
    one, whose denominator also contains ``undecided`` outcomes counted as failures. Other
    states (``unlabelled``, ``not_applicable``, ``unavailable``, ``no_prediction``) stay out of
    both rates."""
    if metric not in BINARY_METRICS:
        return None
    scored: list[float] = []
    undecided = 0
    for r in results:
        outcome = r.metrics[metric]
        if outcome.state == "scored" and outcome.value is not None:
            if outcome.value not in (0.0, 1.0):
                raise ValueError(f"binary metric {metric!r} has non-0/1 value {outcome.value}")
            scored.append(outcome.value)
        elif outcome.state == "undecided":
            undecided += 1
    successes = int(sum(scored))
    denominator = len(scored) + undecided
    return {
        "scored_n": len(scored),
        "undecided_n": undecided,
        "successes": successes,
        "measured_rate": None if not scored else round(successes / len(scored), 6),
        "conservative_rate": None if denominator == 0 else round(successes / denominator, 6),
        "note": "undecided counted as failure; applies to this explicitly binary metric only",
    }
