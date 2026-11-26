"""Tests for the deterministic MetroPT-3 SCM feature slice."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from factorymind.anomaly.features import WindowSpec, block_features
from factorymind.causal.metropt3 import METROPT3_AIR_LEAKAGE_GRAPH
from factorymind.causal.scm import METROPT3_FEATURE_SCM, CausalStructuralModel
from tests.conftest import simulate_apu


def test_scm_dependencies_validate_and_synthetic_example_reproduces_features() -> None:
    telemetry = simulate_apu("2020-01-01", 60, seed=19)
    original = telemetry.copy(deep=True)

    METROPT3_FEATURE_SCM.validate_against(METROPT3_AIR_LEAKAGE_GRAPH)
    result = METROPT3_FEATURE_SCM.evaluate_window(telemetry, graph=METROPT3_AIR_LEAKAGE_GRAPH)
    existing_features = block_features(telemetry, WindowSpec())

    assert result["metropt3.load_duty"] == pytest.approx(existing_features["load_duty"])
    expected_slope = existing_features["unloaded_tp3_slope"]
    assert result["metropt3.unloaded_tp3_slope"] == (
        pytest.approx(expected_slope) if pd.notna(expected_slope) else None
    )
    assert all(value is None or isinstance(value, float) for value in result.values())
    pd.testing.assert_frame_equal(telemetry, original)


def test_scm_rejects_unknown_equation_dependencies() -> None:
    data = METROPT3_FEATURE_SCM.model_dump()
    data["variable_ids"].remove("metropt3.motor_current")
    data["exogenous_variable_ids"].remove("metropt3.motor_current")
    with pytest.raises(ValidationError, match="Unknown source node"):
        CausalStructuralModel.model_validate(data)


def test_scm_rejects_equations_with_inconsistent_variable_dependencies() -> None:
    data = METROPT3_FEATURE_SCM.model_dump()
    data["equations"][0]["parent_ids"] = ["metropt3.tp3_pressure"]
    with pytest.raises(ValidationError, match="requires parents"):
        CausalStructuralModel.model_validate(data)


def test_scm_rejects_a_graph_with_different_identity() -> None:
    data = METROPT3_FEATURE_SCM.model_dump()
    data["graph_id"] = "metropt3.different-graph.v1"
    model = CausalStructuralModel.model_validate(data)
    with pytest.raises(ValueError, match="must match"):
        model.validate_against(METROPT3_AIR_LEAKAGE_GRAPH)
