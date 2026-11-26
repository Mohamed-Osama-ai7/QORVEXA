"""Tests for causal variable metadata and evidence-tagged graph validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factorymind.causal import CausalEdge, CausalGraph, CausalVariable
from factorymind.causal.metropt3 import METROPT3_AIR_LEAKAGE_GRAPH


def _variable(variable_id: str) -> CausalVariable:
    return CausalVariable(
        variable_id=variable_id,
        description="A measured pressure variable.",
        role="measured",
        unit="bar",
        source_refs=["UCI #791:TP3"],
        time_reference="Per-sample timestamp.",
    )


def _edge(edge_id: str, source_id: str, target_id: str) -> CausalEdge:
    return CausalEdge(
        edge_id=edge_id,
        source_id=source_id,
        target_id=target_id,
        relation="may_be_associated_with",
        epistemic_type="correlational_indicator",
        evidence_status="observational_association",
        provenance_refs=["analysis:observational"],
        assumptions=["The observations refer to comparable operating periods."],
    )


def test_causal_variable_requires_stable_identity_source_units_and_time_reference() -> None:
    variable = _variable("metropt3.tp3_pressure")
    assert variable.variable_id == "metropt3.tp3_pressure"
    assert variable.role == "measured"
    assert variable.unit == "bar"
    assert variable.source_refs == ["UCI #791:TP3"]

    with pytest.raises(ValidationError):
        CausalVariable(
            variable_id="TP3",
            description="",
            role="unknown",
            unit=None,
            source_refs=[],
            time_reference="",
        )


def test_causal_graph_accepts_typed_dag_with_edge_provenance_and_assumptions() -> None:
    graph = CausalGraph(
        graph_id="test.graph.v1",
        asset_id="metropt3-apu-1",
        dataset_id="metropt3",
        variables=[_variable("metropt3.tp3_pressure"), _variable("metropt3.load_duty")],
        edges=[_edge("assoc.tp3-to-duty", "metropt3.tp3_pressure", "metropt3.load_duty")],
    )
    assert graph.edges[0].provenance_refs
    assert graph.edges[0].assumptions
    assert "not proof of causation" in graph.interpretation_boundary


@pytest.mark.parametrize(
    ("variables", "edges", "message"),
    [
        (["metropt3.tp3_pressure", "metropt3.tp3_pressure"], [], "variable identifiers"),
        (
            ["metropt3.tp3_pressure", "metropt3.load_duty"],
            [_edge("assoc.unknown", "metropt3.tp3_pressure", "metropt3.missing")],
            "Unknown target node",
        ),
        (
            ["metropt3.tp3_pressure", "metropt3.load_duty"],
            [
                _edge("assoc.forward", "metropt3.tp3_pressure", "metropt3.load_duty"),
                _edge("assoc.reverse", "metropt3.load_duty", "metropt3.tp3_pressure"),
            ],
            "acyclic",
        ),
    ],
)
def test_causal_graph_rejects_duplicate_or_invalid_structure(
    variables: list[str], edges: list[CausalEdge], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CausalGraph(
            graph_id="test.graph.v1",
            asset_id="metropt3-apu-1",
            dataset_id="metropt3",
            variables=[_variable(variable_id) for variable_id in variables],
            edges=edges,
        )


def test_edges_require_provenance_and_assumptions() -> None:
    with pytest.raises(ValidationError):
        CausalEdge(
            edge_id="hyp.invalid",
            source_id="metropt3.a",
            target_id="metropt3.b",
            relation="may_affect",
            epistemic_type="causal_hypothesis",
            evidence_status="domain_hypothesis",
            provenance_refs=[],
            assumptions=[],
        )


def test_metropt3_starter_graph_marks_relationships_as_hypotheses_or_weak_evidence() -> None:
    graph = METROPT3_AIR_LEAKAGE_GRAPH
    assert graph.asset_id == "metropt3-apu-1"
    assert all(edge.provenance_refs and edge.assumptions for edge in graph.edges)
    assert all(
        edge.evidence_status in {"domain_hypothesis", "weak_observational_support"}
        for edge in graph.edges
    )
    assert any(variable.role == "latent" for variable in graph.variables)
    assert any(variable.role == "outcome" for variable in graph.variables)
