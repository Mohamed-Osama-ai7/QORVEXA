"""Tests for strict raw-JSON ingress scanner for GeneralizedProblemSpec."""

import json

import pytest

from qorvexa.solver_fabric.raw_ingress import (
    ALIAS_COLLISION,
    ARRAY_TOO_LONG,
    DUPLICATE_KEY,
    INPUT_TOO_LARGE,
    INVALID_ENCODING,
    INVALID_JSON,
    M1_VALIDATION_FAILED,
    NESTING_TOO_DEEP,
    NON_FINITE_NUMBER,
    NON_OBJECT_ROOT,
    NUMBER_NOT_EXACT_IN_M1,
    NUMBER_OVERFLOW,
    NUMBER_UNDERFLOW,
    OBJECT_TOO_WIDE,
    STRING_TOO_LONG,
    TOO_MANY_NODES,
    UNKNOWN_FIELD,
    UNKNOWN_NODE_TYPE,
    UNPAIRED_SURROGATE,
    RawIngressError,
    RawIngressLimits,
    parse_problem_spec_json,
    scan_problem_spec_json,
)


def _valid_spec_dict() -> dict[str, object]:
    return {
        "schemaVersion": "1.1.0",
        "problemId": "lp_test_01",
        "title": "Minimal Test LP",
        "problemDescription": "Minimal test linear programming model",
        "problemType": "linear_programming",
        "formulationStatus": "Draft Ready for Solver Review",
        "mathematicallyVerified": False,
        "timeHorizon": {
            "isSpecified": True,
            "duration": "1",
            "timeStep": "1",
            "unit": "hours",
        },
        "decisionVariables": [
            {
                "id": "x",
                "name": "Flow x",
                "symbol": "x",
                "domain": "continuous",
                "lowerBound": 0.0,
                "upperBound": 10.0,
            }
        ],
        "objectives": [
            {
                "id": "OBJ-01",
                "name": "Total Cost",
                "direction": "minimize",
                "expressionSummary": "x",
                "priorityOrder": 1,
                "expressionAst": {
                    "type": "literal",
                    "dataType": "real",
                    "value": 1.0,
                },
            }
        ],
        "constraints": [
            {
                "id": "CON-01",
                "name": "Flow limit",
                "category": "operational",
                "relation": "<=",
                "expressionSummary": "x <= 5.0",
                "boundValue": 5.0,
                "expressionAst": {
                    "type": "variable_ref",
                    "variableId": "x",
                },
            }
        ],
        "solverHandoffNotes": {
            "recommendedSolverFamily": "linear_programming",
            "readinessForMathematicalSolver": True,
        },
    }


class TestRawIngressValid:
    """Verifies that valid GeneralizedProblemSpec JSON passes scanning and parsing."""

    def test_valid_spec_dict_passes(self) -> None:
        payload = json.dumps(_valid_spec_dict())
        doc = scan_problem_spec_json(payload)
        assert doc["problemId"] == "lp_test_01"
        spec = parse_problem_spec_json(payload)
        assert spec.problem_id == "lp_test_01"
        assert len(spec.decision_variables) == 1
        assert spec.decision_variables[0].id == "x"

    def test_valid_bytes_input(self) -> None:
        raw_bytes = json.dumps(_valid_spec_dict()).encode("utf-8")
        doc = scan_problem_spec_json(raw_bytes)
        assert doc["schemaVersion"] == "1.1.0"
        spec = parse_problem_spec_json(raw_bytes)
        assert spec.schema_version == "1.1.0"


class TestRawIngressRejections:
    """Verifies strict raw-ingress rejection rules and stable error codes."""

    def test_non_utf8_encoding_rejected(self) -> None:
        bad_bytes = b'{"schemaVersion": "\xff\xfe"}'
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_bytes)
        assert exc_info.value.code == INVALID_ENCODING

    def test_unpaired_surrogate_rejected(self) -> None:
        bad_text = '{"schemaVersion": "1.1.0", "title": "\ud800"}'
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_text)
        assert exc_info.value.code == UNPAIRED_SURROGATE

    def test_invalid_json_rejected(self) -> None:
        bad_json = '{"schemaVersion": "1.1.0", '
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == INVALID_JSON

    def test_non_object_root_rejected(self) -> None:
        for root in ("[1, 2, 3]", '"string_root"', "42", "true"):
            with pytest.raises(RawIngressError) as exc_info:
                scan_problem_spec_json(root)
            assert exc_info.value.code == NON_OBJECT_ROOT

    def test_input_too_large_rejected(self) -> None:
        payload = json.dumps(_valid_spec_dict())
        limits = RawIngressLimits(max_bytes=50)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(payload, limits=limits)
        assert exc_info.value.code == INPUT_TOO_LARGE

    def test_nesting_too_deep_rejected(self) -> None:
        deep_expr: dict[str, object] = {"type": "literal", "dataType": "real", "value": 1.0}
        for _ in range(30):
            deep_expr = {"type": "unary_op", "operator": "neg", "argument": deep_expr}
        data = _valid_spec_dict()
        objs = data["objectives"]
        assert isinstance(objs, list)
        obj0 = objs[0]
        assert isinstance(obj0, dict)
        obj0["expressionAst"] = deep_expr
        limits = RawIngressLimits(max_depth=15)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data), limits=limits)
        assert exc_info.value.code == NESTING_TOO_DEEP

    def test_duplicate_key_rejected(self) -> None:
        bad_json = (
            '{"schemaVersion": "1.1.0", "title": "t1", "title": "t2", '
            '"problemDescription": "desc", '
            '"problemType": "linear_programming", '
            '"formulationStatus": "Draft Ready for Solver Review"}'
        )
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == DUPLICATE_KEY

    def test_duplicate_key_via_escaped_unicode_rejected(self) -> None:
        bad_json = '{"title": "t1", "t\\u0069tle": "t2"}'
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == DUPLICATE_KEY

    def test_unknown_field_rejected(self) -> None:
        data = _valid_spec_dict()
        data["unauthorizedExtraProperty"] = "malicious_payload"
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data))
        assert exc_info.value.code == UNKNOWN_FIELD

    def test_unknown_field_in_nested_model_rejected(self) -> None:
        data = _valid_spec_dict()
        vars_list = data["decisionVariables"]
        assert isinstance(vars_list, list)
        var_entry = vars_list[0]
        assert isinstance(var_entry, dict)
        var_entry["extra_var_field"] = 999
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data))
        assert exc_info.value.code == UNKNOWN_FIELD

    def test_alias_collision_rejected(self) -> None:
        bad_json = (
            '{"schemaVersion": "1.1.0", "schema_version": "1.1.0", '
            '"title": "t", "problemDescription": "desc", '
            '"problemType": "linear_programming", '
            '"formulationStatus": "Draft Ready for Solver Review"}'
        )
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == ALIAS_COLLISION

    def test_non_finite_number_literal_rejected(self) -> None:
        for val in ("NaN", "Infinity", "-Infinity"):
            bad_json = f'{{"schemaVersion": "1.1.0", "title": "t", "value": {val}}}'
            with pytest.raises(RawIngressError) as exc_info:
                scan_problem_spec_json(bad_json)
            assert exc_info.value.code in (NON_FINITE_NUMBER, INVALID_JSON)

    def test_number_overflow_rejected(self) -> None:
        bad_json = '{"schemaVersion": "1.1.0", "title": "t", "value": 1e9999}'
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == NUMBER_OVERFLOW

    def test_number_underflow_rejected(self) -> None:
        bad_json = '{"schemaVersion": "1.1.0", "title": "t", "value": 1e-9999}'
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(bad_json)
        assert exc_info.value.code == NUMBER_UNDERFLOW

    def test_integer_exceeding_53_bits_for_float_field_rejected(self) -> None:
        # 2**53 + 1 = 9007199254740993. On a float field like lowerBound, this would lose precision.
        data = _valid_spec_dict()
        vars_list = data["decisionVariables"]
        assert isinstance(vars_list, list)
        var_entry = vars_list[0]
        assert isinstance(var_entry, dict)
        var_entry["lowerBound"] = 9007199254740993
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data))
        assert exc_info.value.code == NUMBER_NOT_EXACT_IN_M1

    def test_unknown_node_type_in_expression_rejected(self) -> None:
        data = _valid_spec_dict()
        objs = data["objectives"]
        assert isinstance(objs, list)
        obj0 = objs[0]
        assert isinstance(obj0, dict)
        obj0["expressionAst"] = {
            "type": "quantum_gate_operator",
            "qubit": 0,
        }
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data))
        assert exc_info.value.code == UNKNOWN_NODE_TYPE

    def test_array_too_long_rejected(self) -> None:
        data = _valid_spec_dict()
        data["decisionVariables"] = [
            {"id": f"x{i}", "name": f"x{i}", "symbol": f"x{i}", "domain": "continuous"}
            for i in range(20)
        ]
        limits = RawIngressLimits(max_array_len=10)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data), limits=limits)
        assert exc_info.value.code == ARRAY_TOO_LONG

    def test_too_many_nodes_rejected(self) -> None:
        data = _valid_spec_dict()
        limits = RawIngressLimits(max_nodes=10)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data), limits=limits)
        assert exc_info.value.code == TOO_MANY_NODES

    def test_object_too_wide_rejected(self) -> None:
        data = _valid_spec_dict()
        limits = RawIngressLimits(max_object_keys=3)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data), limits=limits)
        assert exc_info.value.code == OBJECT_TOO_WIDE

    def test_string_too_long_rejected(self) -> None:
        data = _valid_spec_dict()
        data["title"] = "A" * 100
        limits = RawIngressLimits(max_string_chars=50)
        with pytest.raises(RawIngressError) as exc_info:
            scan_problem_spec_json(json.dumps(data), limits=limits)
        assert exc_info.value.code == STRING_TOO_LONG

    def test_m1_validation_failure_wrapped(self) -> None:
        # Passes scanner shape but fails M1 validation (e.g. invalid problemType)
        data = _valid_spec_dict()
        data["problemType"] = "invalid_nonexistent_problem_type"
        with pytest.raises(RawIngressError) as exc_info:
            parse_problem_spec_json(json.dumps(data))
        assert exc_info.value.code == M1_VALIDATION_FAILED
