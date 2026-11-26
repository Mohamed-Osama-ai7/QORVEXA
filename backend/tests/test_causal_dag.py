"""Tests for causal DAG structural validation."""

from __future__ import annotations

import pytest

from factorymind.causal import validate_dag


def test_validate_dag_accepts_empty_and_acyclic_graphs() -> None:
    assert validate_dag([], []) is None
    assert (
        validate_dag(
            ["sensor", "load", "pressure", "alarm"],
            [
                ("sensor", "load"),
                ("load", "pressure"),
                ("pressure", "alarm"),
                ("sensor", "pressure"),
            ],
        )
        is None
    )


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([("missing", "known")], "Unknown source node"),
        ([("known", "missing")], "Unknown target node"),
    ],
)
def test_validate_dag_rejects_edges_with_unknown_nodes(
    edges: list[tuple[str, str]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_dag(["known"], edges)


@pytest.mark.parametrize(
    "edges",
    [
        [("a", "a")],
        [("a", "b"), ("b", "c"), ("c", "a")],
    ],
)
def test_validate_dag_rejects_cycles(edges: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError, match="acyclic"):
        validate_dag(["a", "b", "c"], edges)


def test_validate_dag_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValueError, match="unique identifiers"):
        validate_dag(["sensor", "sensor"], [])
