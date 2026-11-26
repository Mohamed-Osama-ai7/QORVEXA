"""Structural validation helpers for directed acyclic graphs."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable


def validate_dag(node_ids: Iterable[str], edges: Iterable[tuple[str, str]]) -> None:
    """Raise ``ValueError`` unless directed edges over the supplied nodes form a DAG.

    This validates graph structure only; it does not establish that edges represent
    valid causal relationships.
    """
    ordered_nodes = list(node_ids)
    nodes = set(ordered_nodes)
    if len(nodes) != len(ordered_nodes):
        raise ValueError("node_ids must contain unique identifiers")

    outgoing: dict[str, set[str]] = {node_id: set() for node_id in ordered_nodes}
    indegree = dict.fromkeys(ordered_nodes, 0)

    for source, target in edges:
        if source not in nodes:
            raise ValueError(f"Unknown source node: {source!r}")
        if target not in nodes:
            raise ValueError(f"Unknown target node: {target!r}")
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    ready = deque(node_id for node_id in ordered_nodes if indegree[node_id] == 0)
    visited = 0

    while ready:
        source = ready.popleft()
        visited += 1
        for target in outgoing[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)

    if visited != len(ordered_nodes):
        raise ValueError("Directed edges must form an acyclic graph")
