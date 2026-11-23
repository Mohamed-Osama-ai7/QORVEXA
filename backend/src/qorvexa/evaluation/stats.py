"""Statistical reliability and uncertainty intervals for QORVEXA evaluation metrics.

Rules (Task 03B):
* Seeded, deterministic bootstrap: default 2,000 resamples, 95% confidence intervals.
* Cluster bootstrap for dependent observations (e.g. multiple queries per asset
  or multi-window incidents).
* Wilson score intervals for binary metrics only when observations are independent.
* Paired bootstrap for comparing two retrievers or models over identical cases.
* Minimum thresholds:
  - Standard bootstrap CI: N >= 30 scored items (and K >= 10 clusters if clustered).
  - Binary exploratory Wilson interval: 10 <= N <= 29 independent observations.
  - Otherwise: state='unavailable' with explicit human-readable reasons.
* Never silently fall back to item-level bootstrap when clusters are provided.
* Degenerate intervals (zero variance, point mass) are detected and explicitly labeled.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_SEED = 42
MIN_BOOTSTRAP_SAMPLES = 30
MIN_BOOTSTRAP_CLUSTERS = 10
MIN_WILSON_SAMPLES = 10
EXPLORATORY_WILSON_MAX = 29

IntervalMethod = Literal[
    "bootstrap_percentile",
    "cluster_bootstrap_percentile",
    "wilson_score",
    "wilson_exploratory",
    "paired_bootstrap",
    "none",
]
IntervalState = Literal["scored", "unavailable", "degenerate"]


class ConfidenceInterval(BaseModel):
    """95% confidence interval and sample metadata for an aggregated metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point_estimate: float | None
    lower: float | None
    upper: float | None
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    method: IntervalMethod
    state: IntervalState
    n_samples: int
    n_clusters: int | None = None
    reason: str | None = None


class PairedComparison(BaseModel):
    """Paired bootstrap comparison between two evaluation runs on identical cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point_difference: float | None  # mean(A) - mean(B)
    lower: float | None
    upper: float | None
    p_value: float | None  # two-sided empirical bootstrap p-value
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    method: Literal["paired_bootstrap", "none"]
    state: IntervalState
    n_pairs: int
    reason: str | None = None


def is_binary_sequence(values: Sequence[float]) -> bool:
    """True if all values are strictly 0.0 or 1.0 (or within float epsilon)."""
    return bool(values) and all(abs(v - 0.0) < 1e-9 or abs(v - 1.0) < 1e-9 for v in values)


def wilson_interval(
    values: Sequence[float],
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    independent: bool = True,
) -> ConfidenceInterval:
    """Calculate Wilson score interval for independent binary observations.

    Exploratory for 10 <= N <= 29; standard for N >= 30.
    Returns 'unavailable' if N < 10, non-binary data, or independence is not justified.
    """
    n = len(values)
    if not independent:
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6) if values else None,
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            reason="wilson_requires_justified_independence",
        )

    if n < MIN_WILSON_SAMPLES:
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6) if values else None,
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            reason=f"insufficient_sample_size: n={n} < {MIN_WILSON_SAMPLES}",
        )

    if not is_binary_sequence(values):
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6),
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            reason="wilson_requires_binary_observations_in_0_1",
        )

    p_hat = float(np.mean(values))
    # z-score for two-sided normal quantile: for 95%, z ~ 1.959964
    # derived analytically: erfinv(confidence_level) * sqrt(2)
    # standard normal quantile via inverse error function approximation
    z = math.sqrt(2.0) * _erfinv(confidence_level)

    denom = 1.0 + (z**2) / n
    center = (p_hat + (z**2) / (2.0 * n)) / denom
    spread = (z / denom) * math.sqrt((p_hat * (1.0 - p_hat) / n) + (z**2) / (4.0 * (n**2)))

    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)

    method: IntervalMethod = "wilson_exploratory" if n <= EXPLORATORY_WILSON_MAX else "wilson_score"
    degenerate = abs(lower - upper) < 1e-9
    state: IntervalState = "degenerate" if degenerate else "scored"
    reason = "zero_width_interval" if degenerate else None

    return ConfidenceInterval(
        point_estimate=round(p_hat, 6),
        lower=round(lower, 6),
        upper=round(upper, 6),
        confidence_level=confidence_level,
        method=method,
        state=state,
        n_samples=n,
        reason=reason,
    )


def bootstrap_interval(
    values: Sequence[float],
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> ConfidenceInterval:
    """Seeded, deterministic percentile bootstrap for independent continuous or binary items.

    Requires N >= 30. Detects zero variance point masses as degenerate.
    """
    n = len(values)
    if n < MIN_BOOTSTRAP_SAMPLES:
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6) if values else None,
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            reason=f"insufficient_sample_size: n={n} < {MIN_BOOTSTRAP_SAMPLES}",
        )

    arr = np.asarray(values, dtype=np.float64)
    point = float(np.mean(arr))

    # Detect exact point-mass (zero variance)
    if np.all(arr == arr[0]):
        return ConfidenceInterval(
            point_estimate=round(point, 6),
            lower=round(point, 6),
            upper=round(point, 6),
            confidence_level=confidence_level,
            method="bootstrap_percentile",
            state="degenerate",
            n_samples=n,
            reason="zero_variance: all observations identical",
        )

    rng = np.random.default_rng(seed)
    boot_indices = rng.integers(0, n, size=(n_resamples, n))
    boot_means = np.mean(arr[boot_indices], axis=1)

    alpha = 1.0 - confidence_level
    lower_pct = 100.0 * (alpha / 2.0)
    upper_pct = 100.0 * (1.0 - alpha / 2.0)

    low, high = np.percentile(boot_means, [lower_pct, upper_pct])
    degenerate = abs(low - high) < 1e-9
    state: IntervalState = "degenerate" if degenerate else "scored"
    reason = "zero_resample_variance" if degenerate else None

    return ConfidenceInterval(
        point_estimate=round(point, 6),
        lower=round(float(low), 6),
        upper=round(float(high), 6),
        confidence_level=confidence_level,
        method="bootstrap_percentile",
        state=state,
        n_samples=n,
        reason=reason,
    )


def cluster_bootstrap_interval(
    values: Sequence[float],
    cluster_ids: Sequence[str | int],
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> ConfidenceInterval:
    """Seeded, deterministic cluster bootstrap for dependent observations.

    Resampling is performed at the cluster level. Requires N >= 30 and K >= 10 clusters.
    CRITICAL RULE: Never silently falls back to item-level bootstrap when K < 10.
    """
    n = len(values)
    if n != len(cluster_ids):
        raise ValueError(f"Length mismatch: {n} values and {len(cluster_ids)} cluster_ids")

    # Group values by cluster
    clusters: dict[str | int, list[float]] = {}
    for v, c in zip(values, cluster_ids, strict=True):
        clusters.setdefault(c, []).append(float(v))

    k = len(clusters)

    if n < MIN_BOOTSTRAP_SAMPLES:
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6) if values else None,
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            n_clusters=k,
            reason=f"insufficient_sample_size: n={n} < {MIN_BOOTSTRAP_SAMPLES}",
        )

    if k < MIN_BOOTSTRAP_CLUSTERS:
        # Strict enforcement: do not fall back to item-level bootstrap!
        return ConfidenceInterval(
            point_estimate=round(float(np.mean(values)), 6),
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=n,
            n_clusters=k,
            reason=f"insufficient_clusters: k={k} < {MIN_BOOTSTRAP_CLUSTERS}",
        )

    point = float(np.mean(values))
    arr_values = np.asarray(values, dtype=np.float64)
    if np.all(arr_values == arr_values[0]):
        return ConfidenceInterval(
            point_estimate=round(point, 6),
            lower=round(point, 6),
            upper=round(point, 6),
            confidence_level=confidence_level,
            method="cluster_bootstrap_percentile",
            state="degenerate",
            n_samples=n,
            n_clusters=k,
            reason="zero_variance: all observations identical across all clusters",
        )

    cluster_keys = list(clusters.keys())
    # Pre-calculate sums and lengths for fast mean calculation during resampling
    cluster_sums = np.array([sum(clusters[c]) for c in cluster_keys], dtype=np.float64)
    cluster_lens = np.array([len(clusters[c]) for c in cluster_keys], dtype=np.int64)

    rng = np.random.default_rng(seed)
    # Resample k cluster indices with replacement
    sampled_indices = rng.integers(0, k, size=(n_resamples, k))

    boot_sums = np.sum(cluster_sums[sampled_indices], axis=1)
    boot_counts = np.sum(cluster_lens[sampled_indices], axis=1)
    boot_means = boot_sums / boot_counts

    alpha = 1.0 - confidence_level
    lower_pct = 100.0 * (alpha / 2.0)
    upper_pct = 100.0 * (1.0 - alpha / 2.0)

    low, high = np.percentile(boot_means, [lower_pct, upper_pct])
    degenerate = abs(low - high) < 1e-9
    state: IntervalState = "degenerate" if degenerate else "scored"
    reason = "zero_resample_variance" if degenerate else None

    return ConfidenceInterval(
        point_estimate=round(point, 6),
        lower=round(float(low), 6),
        upper=round(float(high), 6),
        confidence_level=confidence_level,
        method="cluster_bootstrap_percentile",
        state=state,
        n_samples=n,
        n_clusters=k,
        reason=reason,
    )


def paired_bootstrap_comparison(
    system_a: Sequence[float],
    system_b: Sequence[float],
    *,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> PairedComparison:
    """Paired bootstrap comparison for two systems on identical cases.

    Estimates 95% CI of the difference [mean(A) - mean(B)] and two-sided empirical p-value.
    Requires N >= 30 paired observations.
    """
    n = len(system_a)
    if n != len(system_b):
        raise ValueError(
            f"Length mismatch: {n} system_a values and {len(system_b)} system_b values"
        )

    if n < MIN_BOOTSTRAP_SAMPLES:
        return PairedComparison(
            point_difference=(
                round(float(np.mean(system_a) - np.mean(system_b)), 6) if n > 0 else None
            ),
            lower=None,
            upper=None,
            p_value=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_pairs=n,
            reason=f"insufficient_paired_samples: n={n} < {MIN_BOOTSTRAP_SAMPLES}",
        )

    a = np.asarray(system_a, dtype=np.float64)
    b = np.asarray(system_b, dtype=np.float64)
    diffs = a - b
    point_diff = float(np.mean(diffs))

    if np.all(diffs == diffs[0]):
        return PairedComparison(
            point_difference=round(point_diff, 6),
            lower=round(point_diff, 6),
            upper=round(point_diff, 6),
            p_value=1.0 if point_diff == 0.0 else 0.0,
            confidence_level=confidence_level,
            method="paired_bootstrap",
            state="degenerate",
            n_pairs=n,
            reason="zero_variance: all paired differences identical",
        )

    rng = np.random.default_rng(seed)
    boot_indices = rng.integers(0, n, size=(n_resamples, n))
    boot_diff_means = np.mean(diffs[boot_indices], axis=1)

    alpha = 1.0 - confidence_level
    lower_pct = 100.0 * (alpha / 2.0)
    upper_pct = 100.0 * (1.0 - alpha / 2.0)

    low, high = np.percentile(boot_diff_means, [lower_pct, upper_pct])

    # Two-sided empirical p-value: fraction of bootstrap replicates crossing 0
    p_le = np.mean(boot_diff_means <= 0.0)
    p_ge = np.mean(boot_diff_means >= 0.0)
    empirical_p = min(1.0, 2.0 * min(float(p_le), float(p_ge)))

    degenerate = abs(low - high) < 1e-9
    state: IntervalState = "degenerate" if degenerate else "scored"
    reason = "zero_resample_variance" if degenerate else None

    return PairedComparison(
        point_difference=round(point_diff, 6),
        lower=round(float(low), 6),
        upper=round(float(high), 6),
        p_value=round(empirical_p, 4),
        confidence_level=confidence_level,
        method="paired_bootstrap",
        state=state,
        n_pairs=n,
        reason=reason,
    )


def compute_metric_interval(
    values: Sequence[float | None],
    *,
    cluster_ids: Sequence[str | int] | None = None,
    independent: bool = True,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> ConfidenceInterval:
    """Smart dispatcher to calculate the appropriate confidence interval.

    * Filters out None (unscored/unavailable) outcomes.
    * If cluster_ids are provided: executes cluster bootstrap.
    * If observations are independent binary and 10 <= N <= 29: exploratory Wilson interval.
    * If observations are independent and N >= 30: standard percentile bootstrap.
    * Otherwise: returns unavailable.
    """
    if cluster_ids is not None and len(values) != len(cluster_ids):
        raise ValueError("values and cluster_ids must have matching lengths")

    # Filter to scored items only
    filtered_values: list[float] = []
    filtered_clusters: list[str | int] = []

    for i, v in enumerate(values):
        if v is not None:
            filtered_values.append(float(v))
            if cluster_ids is not None:
                filtered_clusters.append(cluster_ids[i])

    n = len(filtered_values)
    if n == 0:
        return ConfidenceInterval(
            point_estimate=None,
            lower=None,
            upper=None,
            confidence_level=confidence_level,
            method="none",
            state="unavailable",
            n_samples=0,
            n_clusters=0 if cluster_ids is not None else None,
            reason="no_scored_observations",
        )

    if cluster_ids is not None:
        return cluster_bootstrap_interval(
            filtered_values,
            filtered_clusters,
            confidence_level=confidence_level,
            n_resamples=n_resamples,
            seed=seed,
        )

    # Independent observations
    if (
        is_binary_sequence(filtered_values)
        and independent
        and MIN_WILSON_SAMPLES <= n <= EXPLORATORY_WILSON_MAX
    ):
        return wilson_interval(
            filtered_values, confidence_level=confidence_level, independent=independent
        )

    return bootstrap_interval(
        filtered_values,
        confidence_level=confidence_level,
        n_resamples=n_resamples,
        seed=seed,
    )


# Accurate inverse error function for normal quantile calculations (Winitzki approximation)
def _erfinv(x: float) -> float:
    """Inverse error function approximation with relative error < 0.00012 across (-1, 1)."""
    if x <= -1.0:
        return float("-inf")
    if x >= 1.0:
        return float("inf")
    if abs(x) < 1e-12:
        return 0.0

    a = 0.147
    log_term = math.log(1.0 - x * x)
    term1 = 2.0 / (math.pi * a) + log_term / 2.0
    inner = term1 * term1 - log_term / a
    sqrt_inner = math.sqrt(inner)
    val = math.sqrt(sqrt_inner - term1)
    return math.copysign(val, x)
