"""Tests for evaluation statistical reliability, bootstrap, and Wilson intervals (Task 03B)."""

from __future__ import annotations

from qorvexa.evaluation.stats import (
    bootstrap_interval,
    cluster_bootstrap_interval,
    compute_metric_interval,
    is_binary_sequence,
    paired_bootstrap_comparison,
    wilson_interval,
)

# --- 1. Binary checks & Wilson intervals ---------------------------------------------------


def test_is_binary_sequence() -> None:
    assert is_binary_sequence([0.0, 1.0, 1.0, 0.0])
    assert is_binary_sequence([1.0])
    assert not is_binary_sequence([0.0, 0.5, 1.0])
    assert not is_binary_sequence([])


def test_wilson_interval_exploratory_known_answer() -> None:
    # 20 observations, 15 successes (p_hat = 0.75)
    vals = [1.0] * 15 + [0.0] * 5
    res = wilson_interval(vals, confidence_level=0.95, independent=True)
    assert res.state == "scored"
    assert res.method == "wilson_exploratory"
    assert res.n_samples == 20
    assert res.point_estimate == 0.75
    # Standard 95% Wilson interval for 15/20 is ~ [0.531, 0.888]
    assert res.lower is not None and res.upper is not None
    assert 0.52 <= res.lower <= 0.55
    assert 0.87 <= res.upper <= 0.90


def test_wilson_interval_refusals() -> None:
    # N < 10 -> unavailable
    res_small = wilson_interval([1.0] * 5 + [0.0] * 4)
    assert res_small.state == "unavailable"
    assert "insufficient_sample_size" in str(res_small.reason)

    # non-binary -> unavailable
    res_non_binary = wilson_interval([0.2] * 15)
    assert res_non_binary.state == "unavailable"
    assert "binary_observations" in str(res_non_binary.reason)

    # independent = False -> unavailable
    res_dep = wilson_interval([1.0] * 15 + [0.0] * 5, independent=False)
    assert res_dep.state == "unavailable"
    assert "justified_independence" in str(res_dep.reason)


# --- 2. Standard Bootstrap -----------------------------------------------------------------


def test_bootstrap_interval_sample_size_threshold() -> None:
    # N = 29 -> unavailable
    small = [0.5] * 29
    res_small = bootstrap_interval(small)
    assert res_small.state == "unavailable"
    assert res_small.lower is None and res_small.upper is None
    assert "insufficient_sample_size: n=29 < 30" in str(res_small.reason)

    # N = 30 -> scored
    vals = [0.1 * (i % 10) for i in range(30)]
    res_ok = bootstrap_interval(vals, seed=42)
    assert res_ok.state == "scored"
    assert res_ok.method == "bootstrap_percentile"
    assert res_ok.n_samples == 30
    assert res_ok.lower is not None and res_ok.upper is not None
    assert res_ok.lower < res_ok.point_estimate < res_ok.upper


def test_bootstrap_interval_determinism() -> None:
    vals = [0.2, 0.4, 0.6, 0.8, 1.0] * 10  # N = 50
    run1 = bootstrap_interval(vals, seed=123)
    run2 = bootstrap_interval(vals, seed=123)
    run3 = bootstrap_interval(vals, seed=999)

    assert run1 == run2
    assert (run1.lower, run1.upper) != (run3.lower, run3.upper)


def test_bootstrap_interval_degenerate_point_mass() -> None:
    # All 1.0 -> zero variance
    all_ones = [1.0] * 35
    res = bootstrap_interval(all_ones)
    assert res.state == "degenerate"
    assert res.point_estimate == 1.0
    assert res.lower == 1.0 and res.upper == 1.0
    assert "zero_variance" in str(res.reason)


# --- 3. Cluster Bootstrap ------------------------------------------------------------------


def test_cluster_bootstrap_insufficient_clusters_never_falls_back() -> None:
    # N = 40, but only K = 4 clusters
    vals = [0.2, 0.4, 0.6, 0.8] * 10
    clusters = ["c1", "c2", "c3", "c4"] * 10

    res = cluster_bootstrap_interval(vals, clusters)
    # Must NOT fall back to item-level bootstrap!
    assert res.state == "unavailable"
    assert res.method == "none"
    assert "insufficient_clusters: k=4 < 10" in str(res.reason)


def test_cluster_bootstrap_valid_k_clusters() -> None:
    # N = 45, K = 15 clusters
    vals = [float(i % 5) / 5.0 for i in range(45)]
    clusters = [f"cluster_{i % 15}" for i in range(45)]

    res = cluster_bootstrap_interval(vals, clusters, seed=42)
    assert res.state == "scored"
    assert res.method == "cluster_bootstrap_percentile"
    assert res.n_samples == 45
    assert res.n_clusters == 15
    assert res.lower is not None and res.upper is not None
    assert res.lower <= res.point_estimate <= res.upper


def test_cluster_bootstrap_degenerate() -> None:
    vals = [1.0] * 40
    clusters = [f"cluster_{i % 10}" for i in range(40)]
    res = cluster_bootstrap_interval(vals, clusters)
    assert res.state == "degenerate"
    assert res.point_estimate == 1.0
    assert res.lower == 1.0 and res.upper == 1.0


# --- 4. Paired Bootstrap Comparison --------------------------------------------------------


def test_paired_bootstrap_comparison() -> None:
    # N = 40 with varying positive difference
    sys_a = [0.8 + 0.03 * (i % 5) for i in range(40)]
    sys_b = [0.7 + 0.01 * (i % 4) for i in range(40)]

    comp = paired_bootstrap_comparison(sys_a, sys_b, seed=42)
    assert comp.state == "scored"
    assert comp.n_pairs == 40
    assert comp.point_difference is not None and comp.point_difference > 0.0
    assert comp.lower is not None and comp.upper is not None
    assert comp.lower > 0.0  # strictly positive difference
    assert comp.p_value == 0.0  # p < 0.001


def test_paired_bootstrap_sample_size_refusal() -> None:
    sys_a = [1.0] * 20
    sys_b = [0.5] * 20
    comp = paired_bootstrap_comparison(sys_a, sys_b)
    assert comp.state == "unavailable"
    assert "insufficient_paired_samples: n=20 < 30" in str(comp.reason)


# --- 5. Dispatcher: compute_metric_interval ------------------------------------------------


def test_compute_metric_interval_dispatcher() -> None:
    # Case A: Empty / all None
    assert compute_metric_interval([None, None]).state == "unavailable"

    # Case B: Binary, N=15, independent -> exploratory Wilson
    bin_15 = [1.0, 0.0] * 7 + [1.0]
    res_b = compute_metric_interval(bin_15)
    assert res_b.method == "wilson_exploratory"
    assert res_b.state == "scored"

    # Case C: Continuous, N=35 -> Percentile bootstrap
    cont_35 = [0.1 * (i % 8) for i in range(35)]
    res_c = compute_metric_interval(cont_35)
    assert res_c.method == "bootstrap_percentile"
    assert res_c.state == "scored"

    # Case D: Clustered, K=12, N=36 -> Cluster bootstrap
    clusters = [f"c{i % 12}" for i in range(36)]
    res_d = compute_metric_interval([*cont_35, 0.5], cluster_ids=clusters)
    assert res_d.method == "cluster_bootstrap_percentile"
    assert res_d.state == "scored"
