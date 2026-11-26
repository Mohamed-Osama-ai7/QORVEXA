"""Minimal deterministic SCM feature slice using the existing MetroPT-3 feature pipeline."""

from __future__ import annotations

from typing import Final, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factorymind.anomaly.features import WindowSpec, block_features
from factorymind.causal.dag import validate_dag
from factorymind.causal.metropt3 import METROPT3_AIR_LEAKAGE_GRAPH
from factorymind.causal.schema import CausalGraph, CausalIdentifier, NonEmptyText

MechanismId = Literal[
    "metropt3.load_duty.v1",
    "metropt3.unloaded_tp3_slope.v1",
]

_MECHANISM_DEFINITIONS: Final[dict[MechanismId, tuple[str, frozenset[str]]]] = {
    "metropt3.load_duty.v1": (
        "metropt3.load_duty",
        frozenset({"metropt3.motor_current"}),
    ),
    "metropt3.unloaded_tp3_slope.v1": (
        "metropt3.unloaded_tp3_slope",
        frozenset({"metropt3.tp3_pressure", "metropt3.dv_eletric"}),
    ),
}
_MECHANISM_FEATURES: Final[dict[MechanismId, str]] = {
    "metropt3.load_duty.v1": "load_duty",
    "metropt3.unloaded_tp3_slope.v1": "unloaded_tp3_slope",
}


class StructuralEquation(BaseModel):
    """A documented deterministic feature definition, not an estimated causal function."""

    model_config = ConfigDict(extra="forbid")

    equation_id: CausalIdentifier
    target_id: CausalIdentifier
    parent_ids: list[CausalIdentifier] = Field(min_length=1)
    context_inputs: list[NonEmptyText] = Field(default_factory=list)
    mechanism_id: MechanismId
    description: NonEmptyText
    provenance_refs: list[NonEmptyText] = Field(min_length=1)
    assumptions: list[NonEmptyText] = Field(min_length=1)
    evidence_status: Literal["implemented_definition"] = "implemented_definition"

    @model_validator(mode="after")
    def _validate_mechanism(self) -> StructuralEquation:
        expected_target, expected_parents = _MECHANISM_DEFINITIONS[self.mechanism_id]
        if self.target_id != expected_target:
            raise ValueError(f"Mechanism {self.mechanism_id!r} defines target {expected_target!r}")
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("equation parent identifiers must be unique")
        if set(self.parent_ids) != expected_parents:
            raise ValueError(
                f"Mechanism {self.mechanism_id!r} requires parents {sorted(expected_parents)}"
            )
        return self


class CausalStructuralModel(BaseModel):
    """A small deterministic SCM slice over observed telemetry and derived window features."""

    model_config = ConfigDict(extra="forbid")

    scm_id: CausalIdentifier
    graph_id: CausalIdentifier
    dataset_id: CausalIdentifier
    variable_ids: list[CausalIdentifier] = Field(min_length=1)
    exogenous_variable_ids: list[CausalIdentifier] = Field(min_length=1)
    equations: list[StructuralEquation] = Field(min_length=1)
    assumptions: list[NonEmptyText] = Field(min_length=1)
    interpretation_boundary: NonEmptyText = (
        "This model computes existing derived features; it does not estimate causal effects."
    )

    @model_validator(mode="after")
    def _validate_dependencies(self) -> CausalStructuralModel:
        if len(set(self.variable_ids)) != len(self.variable_ids):
            raise ValueError("SCM variable identifiers must be unique")
        if len(set(self.exogenous_variable_ids)) != len(self.exogenous_variable_ids):
            raise ValueError("SCM exogenous variable identifiers must be unique")
        equation_ids = [equation.equation_id for equation in self.equations]
        if len(set(equation_ids)) != len(equation_ids):
            raise ValueError("SCM equation identifiers must be unique")

        targets = [equation.target_id for equation in self.equations]
        if len(set(targets)) != len(targets):
            raise ValueError("Each SCM variable may have at most one structural equation")
        if set(targets) & set(self.exogenous_variable_ids):
            raise ValueError("SCM variables cannot be both exogenous and equation targets")
        if set(targets) | set(self.exogenous_variable_ids) != set(self.variable_ids):
            raise ValueError(
                "Every SCM variable must be declared exogenous or have exactly one equation"
            )

        dependencies = [
            (parent_id, equation.target_id)
            for equation in self.equations
            for parent_id in equation.parent_ids
        ]
        validate_dag(self.variable_ids, dependencies)
        return self

    def validate_against(self, graph: CausalGraph) -> None:
        """Validate graph identity, variable membership, and variable roles."""
        if self.graph_id != graph.graph_id or self.dataset_id != graph.dataset_id:
            raise ValueError("SCM graph_id and dataset_id must match the supplied causal graph")
        by_id = {variable.variable_id: variable for variable in graph.variables}
        missing = sorted(set(self.variable_ids) - set(by_id))
        if missing:
            raise ValueError(f"SCM references variables absent from the graph: {missing}")
        invalid_exogenous = [
            variable_id
            for variable_id in self.exogenous_variable_ids
            if by_id[variable_id].role != "measured"
        ]
        if invalid_exogenous:
            raise ValueError(
                f"SCM exogenous inputs must be measured variables: {invalid_exogenous}"
            )
        invalid_targets = [
            equation.target_id
            for equation in self.equations
            if by_id[equation.target_id].role != "derived"
        ]
        if invalid_targets:
            raise ValueError(
                f"Deterministic SCM equations must target derived variables: {invalid_targets}"
            )

    def evaluate_window(
        self,
        telemetry: pd.DataFrame,
        *,
        graph: CausalGraph,
        spec: WindowSpec | None = None,
    ) -> dict[str, float | None]:
        """Evaluate registered derived features using the existing window implementation."""
        self.validate_against(graph)
        features = block_features(telemetry, spec or WindowSpec())
        values: dict[str, float | None] = {}
        for equation in self.equations:
            value = float(features[_MECHANISM_FEATURES[equation.mechanism_id]])
            values[equation.target_id] = value if np.isfinite(value) else None
        return values


METROPT3_FEATURE_SCM = CausalStructuralModel(
    scm_id="metropt3.deterministic-feature-scm.v1",
    graph_id=METROPT3_AIR_LEAKAGE_GRAPH.graph_id,
    dataset_id=METROPT3_AIR_LEAKAGE_GRAPH.dataset_id,
    variable_ids=[
        "metropt3.tp3_pressure",
        "metropt3.motor_current",
        "metropt3.dv_eletric",
        "metropt3.load_duty",
        "metropt3.unloaded_tp3_slope",
    ],
    exogenous_variable_ids=[
        "metropt3.tp3_pressure",
        "metropt3.motor_current",
        "metropt3.dv_eletric",
    ],
    equations=[
        StructuralEquation(
            equation_id="eq.metropt3.load-duty.v1",
            target_id="metropt3.load_duty",
            parent_ids=["metropt3.motor_current"],
            mechanism_id="metropt3.load_duty.v1",
            description=("Mean indicator(Motor_current > 5.5 A) over a telemetry window."),
            provenance_refs=[
                "backend/src/factorymind/anomaly/features.py:LOADED_CURRENT_A",
                "backend/src/factorymind/anomaly/features.py:_sample_helpers",
            ],
            assumptions=[
                "The project-defined 5.5 A threshold is the operational loaded-state proxy.",
                "The telemetry samples in the window represent the interval being summarized.",
            ],
        ),
        StructuralEquation(
            equation_id="eq.metropt3.unloaded-tp3-slope.v1",
            target_id="metropt3.unloaded_tp3_slope",
            parent_ids=["metropt3.tp3_pressure", "metropt3.dv_eletric"],
            mechanism_id="metropt3.unloaded_tp3_slope.v1",
            description=(
                "Median TP3 per-minute difference over contiguous adjacent samples where "
                "DV_eletric is inactive in both samples."
            ),
            provenance_refs=[
                "backend/src/factorymind/anomaly/features.py:_sample_helpers",
                "backend/src/factorymind/anomaly/features.py:WindowSpec.max_gap_s",
            ],
            context_inputs=[
                "Telemetry DataFrame index: timestamp values used to calculate elapsed time."
            ],
            assumptions=[
                "The time-index values are valid and use a consistent time basis.",
                "DV_eletric inactive is the existing unloaded-interval definition.",
                "A slope is unobservable when no qualifying adjacent sample pair exists.",
            ],
        ),
    ],
    assumptions=[
        "Measured sensor channels are boundary inputs to this feature-computation slice.",
        "This SCM represents existing deterministic feature definitions only, not a physical "
        "causal mechanism or intervention model.",
    ],
)

__all__ = [
    "METROPT3_FEATURE_SCM",
    "CausalStructuralModel",
    "StructuralEquation",
]
