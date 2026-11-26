"""Typed, evidence-tagged causal graph schemas without causal discovery or inference."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from factorymind.causal.dag import validate_dag
from factorymind.contracts.knowledge import EpistemicType

CausalVariableRole = Literal["measured", "derived", "latent", "outcome"]
CausalEvidenceStatus = Literal[
    "domain_hypothesis",
    "weak_observational_support",
    "observational_association",
    "unverified",
]

CausalIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$",
    ),
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CausalVariable(BaseModel):
    """A stable, time-referenced variable in a candidate causal model."""

    model_config = ConfigDict(extra="forbid")

    variable_id: CausalIdentifier
    description: NonEmptyText
    role: CausalVariableRole
    unit: NonEmptyText | None
    source_refs: list[NonEmptyText] = Field(min_length=1)
    time_reference: NonEmptyText


class CausalEdge(BaseModel):
    """A directed candidate relationship with explicit provenance and assumptions."""

    model_config = ConfigDict(extra="forbid")

    edge_id: CausalIdentifier
    source_id: CausalIdentifier
    target_id: CausalIdentifier
    relation: NonEmptyText
    epistemic_type: EpistemicType
    evidence_status: CausalEvidenceStatus
    provenance_refs: list[NonEmptyText] = Field(min_length=1)
    assumptions: list[NonEmptyText] = Field(min_length=1)


class CausalGraph(BaseModel):
    """Directed acyclic graph representation; acyclicity does not prove causal validity."""

    model_config = ConfigDict(extra="forbid")

    graph_id: CausalIdentifier
    asset_id: CausalIdentifier
    dataset_id: CausalIdentifier
    variables: list[CausalVariable] = Field(min_length=1)
    edges: list[CausalEdge]
    interpretation_boundary: NonEmptyText = (
        "Edges are documented hypotheses or associations, not proof of causation."
    )

    @model_validator(mode="after")
    def _validate_graph(self) -> CausalGraph:
        variable_ids = [variable.variable_id for variable in self.variables]
        if len(set(variable_ids)) != len(variable_ids):
            raise ValueError("variable identifiers must be unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("edge identifiers must be unique")
        validate_dag(
            variable_ids,
            [(edge.source_id, edge.target_id) for edge in self.edges],
        )
        return self
