"""Task 03B: interval policy (dependence handling), summary integration, conservative metrics.

``stats.py`` is tested separately (``test_evaluation_stats.py``); these tests cover the policy
layer in ``intervals.py`` and its use by ``runner.summarize``."""

from __future__ import annotations

import pytest

from qorvexa.evaluation.intervals import (
    BINARY_METRICS,
    SAMPLING_NOTE,
    conservative_binary,
    derive_seed,
    metric_interval,
)
from qorvexa.evaluation.runner import summarize
from qorvexa.evaluation.schemas import CaseResult, MetricOutcome

METRIC = "hit_rate_at_5"


def result(
    i: int,
    value: float | None,
    cluster: str | None,
    *,
    state: str = "scored",
    split: str = "development",
    metric: str = METRIC,
) -> CaseResult:
    return CaseResult(
        case_id=f"c{i}",
        task="retrieval",
        provenance="project_document_retrieval",
        status="scored",
        split=split,  # type: ignore[arg-type]
        cluster_id=cluster,
        metrics={metric: MetricOutcome(value=value, state=state)},  # type: ignore[arg-type]
    )


def mixed(n: int, cluster_of: callable[[int], str | None]) -> list[CaseResult]:  # type: ignore[valid-type]
    return [result(i, float(i % 2), cluster_of(i)) for i in range(n)]


def test_unknown_dependence_is_unavailable_whatever_the_sample_size() -> None:
    iv = metric_interval(METRIC, mixed(100, lambda i: None))
    assert iv.state == "unavailable" and iv.method == "none"
    assert iv.lower is None and iv.upper is None
    assert "dependence structure unknown" in (iv.reason or "")
    assert iv.point_estimate == pytest.approx(0.5) and iv.n_samples == 100


def test_partially_unknown_clusters_are_unavailable_not_item_level() -> None:
    iv = metric_interval(METRIC, mixed(60, lambda i: None if i % 10 == 0 else f"c{i}"))
    assert iv.state == "unavailable" and "dependence structure unknown" in (iv.reason or "")


def test_singleton_clusters_are_treated_as_independent() -> None:
    exploratory = metric_interval(METRIC, mixed(12, lambda i: f"own{i}"))
    assert exploratory.method == "wilson_exploratory" and exploratory.lower is not None
    standard = metric_interval(METRIC, mixed(40, lambda i: f"own{i}"))
    assert standard.method == "bootstrap_percentile" and standard.state == "scored"
    tiny = metric_interval(METRIC, mixed(4, lambda i: f"own{i}"))
    assert tiny.state == "unavailable" and tiny.lower is None


def test_shared_clusters_use_the_cluster_bootstrap_never_wilson_or_item_bootstrap() -> None:
    iv = metric_interval(METRIC, mixed(40, lambda i: f"g{i // 4}"))
    assert iv.method == "cluster_bootstrap_percentile" and iv.n_clusters == 10
    few = metric_interval(METRIC, mixed(45, lambda i: f"g{i // 5}"))  # 9 clusters
    assert few.state == "unavailable" and few.lower is None
    small = metric_interval(METRIC, mixed(24, lambda i: f"g{i // 2}"))  # 12 clusters, n=24
    assert small.state == "unavailable"


def test_degenerate_clustered_sample_is_flagged() -> None:
    results = [result(i, 1.0, f"g{i // 4}") for i in range(40)]
    iv = metric_interval(METRIC, results)
    assert iv.state == "degenerate" and "zero_variance" in (iv.reason or "")


def test_only_scored_outcomes_enter_the_interval() -> None:
    results = [result(i, float(i % 2), f"own{i}") for i in range(40)]
    results += [result(100 + i, None, None, state="undecided") for i in range(5)]
    results += [result(200 + i, None, None, state="unlabelled") for i in range(5)]
    iv = metric_interval(METRIC, results)
    assert iv.n_samples == 40 and iv.method == "bootstrap_percentile"


def test_interval_is_deterministic_and_seed_is_metric_specific() -> None:
    results = mixed(40, lambda i: f"g{i // 4}")
    assert metric_interval(METRIC, results) == metric_interval(METRIC, results)
    assert derive_seed("a") == derive_seed("a") != derive_seed("b")


# --- conservative undecided-as-failure ---------------------------------------------------------


def _outcomes(metric: str, spec: list[tuple[str, float | None]]) -> list[CaseResult]:
    return [result(i, v, None, state=s, metric=metric) for i, (s, v) in enumerate(spec)]


def test_conservative_counts_undecided_as_failure_and_leaves_measured_rate_alone() -> None:
    metric = "critical_contradiction_handling"
    res = _outcomes(
        metric, [("scored", 1.0), ("scored", 0.0), ("undecided", None), ("undecided", None)]
    )
    cons = conservative_binary(metric, res)
    assert cons is not None
    assert (cons["measured_rate"], cons["conservative_rate"]) == (0.5, 0.25)
    assert (cons["scored_n"], cons["undecided_n"], cons["successes"]) == (2, 2, 1)
    assert "explicitly binary" in cons["note"]


def test_other_states_stay_out_of_both_rates() -> None:
    metric = "category_match"
    spec = [("scored", 1.0)] + [
        (s, None) for s in ("unlabelled", "not_applicable", "unavailable", "no_prediction")
    ]
    cons = conservative_binary(metric, _outcomes(metric, spec))
    assert cons is not None and cons["measured_rate"] == cons["conservative_rate"] == 1.0
    only_undecided = conservative_binary(metric, _outcomes(metric, [("undecided", None)]))
    assert only_undecided is not None
    assert only_undecided["measured_rate"] is None and only_undecided["conservative_rate"] == 0.0
    nothing = conservative_binary(metric, _outcomes(metric, [("no_prediction", None)]))
    assert nothing is not None and nothing["conservative_rate"] is None


def test_conservative_is_only_for_explicitly_binary_metrics() -> None:
    for metric in ("recall_at_5", "reciprocal_rank_at_10", "supporting_evidence_precision"):
        assert metric not in BINARY_METRICS
        assert (
            conservative_binary(metric, _outcomes(metric, [("scored", 0.5), ("undecided", None)]))
            is None
        )
    with pytest.raises(ValueError, match="non-0/1"):
        conservative_binary("hit_rate_at_5", _outcomes("hit_rate_at_5", [("scored", 0.5)]))


# --- summary integration ------------------------------------------------------------------------


def test_summary_has_intervals_per_split_but_not_for_fixtures() -> None:
    results = mixed(40, lambda i: f"g{i // 4}")
    results += [result(500 + i, float(i % 2), None, split="fixture") for i in range(6)]
    summary = summarize(results, [METRIC])
    assert summary["summary_schema"] == "summary/v1"
    assert (
        summary["interval_note"] == SAMPLING_NOTE and "sampling variability only" in SAMPLING_NOTE
    )
    dev = summary["by_split"]["development"]["metrics"][METRIC]
    assert dev["n_scored"] == 40 and dev["interval"]["method"] == "cluster_bootstrap_percentile"
    assert dev["conservative"]["undecided_n"] == 0
    fixture = summary["by_split"]["fixture"]["metrics"][METRIC]
    assert "interval" not in fixture
    assert summary["overall"]["metrics"][METRIC]["n_scored"] == 46  # overall keeps old semantics


def test_summary_is_deterministic() -> None:
    results = mixed(40, lambda i: f"g{i // 4}")
    assert summarize(results, [METRIC]) == summarize(results, [METRIC])


def test_conservative_does_not_alter_the_original_mean() -> None:
    metric = "critical_contradiction_handling"
    res = _outcomes(metric, [("scored", 1.0), ("scored", 0.0), ("undecided", None)])
    entry = summarize(res, [metric])["overall"]["metrics"][metric]
    assert entry["mean"] == 0.5 and entry["conservative"]["conservative_rate"] == pytest.approx(
        1 / 3, abs=1e-6
    )
    assert entry["states"] == {"scored": 2, "undecided": 1}
