"""Adversarial and malformed-input tests for strict raw-JSON ingress (S3)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from qorvexa.contracts.solver_fabric import GeneralizedProblemSpec, LiteralNode
from qorvexa.solver_fabric.raw_ingress import (
    ALIAS_COLLISION,
    ARRAY_TOO_LONG,
    DUPLICATE_KEY,
    INPUT_TOO_LARGE,
    INTEGER_TOO_LARGE,
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
    TYPE_MISMATCH,
    UNKNOWN_FIELD,
    UNKNOWN_NODE_TYPE,
    UNPAIRED_SURROGATE,
    VERIFICATION_FLAG_NOT_TRUSTED,
    RawIngressError,
    RawIngressLimits,
    parse_problem_spec_json,
    scan_problem_spec_json,
)


def base_doc() -> dict[str, Any]:
    return {
        "title": "t",
        "problemDescription": "d",
        "problemType": "linear_programming",
        "timeHorizon": {"isSpecified": False},
        "solverHandoffNotes": {"recommendedSolverFamily": "lp"},
        "decisionVariables": [
            {
                "id": "VAR-01",
                "name": "x",
                "symbol": "x",
                "domain": "continuous",
                "lowerBound": 0,
                "upperBound": 10,
            }
        ],
        "objectives": [
            {
                "id": "OBJ-01",
                "direction": "minimize",
                "name": "o",
                "expressionSummary": "x",
                "expressionAst": {"type": "variable_ref", "variableId": "VAR-01"},
            }
        ],
        "constraints": [
            {
                "id": "CON-01",
                "name": "c",
                "category": "capacity",
                "relation": "<=",
                "expressionSummary": "x <= 5",
                "expressionAst": {"type": "variable_ref", "variableId": "VAR-01"},
                "boundValue": 5,
            }
        ],
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc)


def code_of(raw: bytes | str, limits: RawIngressLimits | None = None) -> str:
    with pytest.raises(RawIngressError) as exc:
        parse_problem_spec_json(raw, limits)
    return exc.value.code


class TestAcceptsValidDocuments:
    def test_valid_document_roundtrips(self) -> None:
        spec = parse_problem_spec_json(dumps(base_doc()))
        assert isinstance(spec, GeneralizedProblemSpec)
        assert spec.decision_variables[0].id == "VAR-01"
        assert spec.decision_variables[0].upper_bound == 10.0

    def test_bytes_input_accepted(self) -> None:
        assert parse_problem_spec_json(dumps(base_doc()).encode("utf-8")).title == "t"

    def test_snake_case_names_alone_are_accepted(self) -> None:
        doc = base_doc()
        v = doc["decisionVariables"][0]
        v["lower_bound"] = v.pop("lowerBound")
        v["upper_bound"] = v.pop("upperBound")
        spec = parse_problem_spec_json(dumps(doc))
        assert spec.decision_variables[0].upper_bound == 10.0

    def test_expression_summary_is_inert_text(self) -> None:
        doc = base_doc()
        doc["objectives"][0]["expressionSummary"] = "__import__('os').system('echo pwned')"
        spec = parse_problem_spec_json(dumps(doc))
        assert spec.objectives[0].expression_summary == "__import__('os').system('echo pwned')"

    def test_exact_big_integer_literal_survives_json_ingress(self) -> None:
        doc = base_doc()
        big = 10**40 + 1
        doc["objectives"][0]["expressionAst"] = {
            "type": "literal",
            "dataType": "integer",
            "value": big,
        }
        spec = parse_problem_spec_json(dumps(doc))
        node = spec.objectives[0].expression_ast
        assert isinstance(node, LiteralNode)
        assert node.value == big and isinstance(node.value, int)

    def test_unicode_text(self) -> None:
        doc = base_doc()
        doc["title"] = "pump é 中文"
        raw = json.dumps(doc, ensure_ascii=False).encode()
        assert parse_problem_spec_json(raw).title.startswith("pump")

    def test_scan_returns_plain_dict(self) -> None:
        assert isinstance(scan_problem_spec_json(dumps(base_doc())), dict)


class TestDuplicateKeysAndAliases:
    def test_duplicate_top_level_key(self) -> None:
        text = dumps(base_doc()).replace('"title": "t"', '"title": "t", "title": "u"')
        assert code_of(text) == DUPLICATE_KEY

    def test_duplicate_nested_key(self) -> None:
        text = dumps(base_doc()).replace('"id": "VAR-01", "name"', '"id": "A", "id": "B", "name"')
        assert code_of(text) == DUPLICATE_KEY

    def test_duplicate_after_escape_decoding(self) -> None:
        assert code_of('{"title": "a", "tit\\u006ce": "b"}') == DUPLICATE_KEY

    def test_case_variants_are_unknown_fields(self) -> None:
        text = dumps(base_doc()).replace('"title": "t"', '"title": "t", "Title": "u"')
        assert code_of(text) == UNKNOWN_FIELD

    def test_alias_collision_snake_and_camel(self) -> None:
        doc = base_doc()
        doc["decisionVariables"][0]["upper_bound"] = 3
        assert code_of(dumps(doc)) == ALIAS_COLLISION

    def test_alias_collision_even_when_values_equal(self) -> None:
        doc = base_doc()
        doc["decisionVariables"][0]["upper_bound"] = 10
        assert code_of(dumps(doc)) == ALIAS_COLLISION

    def test_alias_collision_in_expression_node(self) -> None:
        doc = base_doc()
        doc["objectives"][0]["expressionAst"]["variable_id"] = "VAR-01"
        assert code_of(dumps(doc)) == ALIAS_COLLISION

    def test_alias_collision_in_nested_model(self) -> None:
        doc = base_doc()
        doc["timeHorizon"]["is_specified"] = True
        assert code_of(dumps(doc)) == ALIAS_COLLISION


class TestUnknownFields:
    def test_unknown_top_level_field(self) -> None:
        doc = base_doc()
        doc["bogus"] = 1
        assert code_of(dumps(doc)) == UNKNOWN_FIELD

    def test_typo_in_bound_name_is_rejected_not_ignored(self) -> None:
        doc = base_doc()
        doc["decisionVariables"][0]["upperBoud"] = 3
        with pytest.raises(RawIngressError) as exc:
            parse_problem_spec_json(dumps(doc))
        assert exc.value.code == UNKNOWN_FIELD
        assert "upperBoud" in exc.value.message
        # The frozen M1 model alone would silently drop the typo:
        m1 = GeneralizedProblemSpec.model_validate(doc)
        assert m1.decision_variables[0].upper_bound == 10.0

    def test_unknown_field_inside_ast_node(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["expressionAst"]["extra"] = 1
        assert code_of(dumps(doc)) == UNKNOWN_FIELD

    def test_unknown_field_in_constraint(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["isVerified"] = True
        assert code_of(dumps(doc)) == UNKNOWN_FIELD

    def test_unknown_field_in_deeply_nested_ast(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["expressionAst"] = {
            "type": "binary_op",
            "operator": "add",
            "left": {"type": "variable_ref", "variableId": "VAR-01"},
            "right": {
                "type": "unary_op",
                "operator": "neg",
                "argument": {"type": "variable_ref", "variableId": "VAR-01", "evil": 1},
            },
        }
        with pytest.raises(RawIngressError) as exc:
            parse_problem_spec_json(dumps(doc))
        assert exc.value.code == UNKNOWN_FIELD
        assert exc.value.path.endswith(".right.argument")

    def test_unknown_field_in_nary_argument_list(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["expressionAst"] = {
            "type": "nary_op",
            "operator": "sum",
            "arguments": [
                {"type": "variable_ref", "variableId": "VAR-01"},
                {"type": "literal", "dataType": "real", "value": 1, "bad": 1},
            ],
        }
        assert code_of(dumps(doc)) == UNKNOWN_FIELD

    def test_unknown_node_type_and_missing_type(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["expressionAst"]["type"] = "evil"
        assert code_of(dumps(doc)) == UNKNOWN_NODE_TYPE
        doc = base_doc()
        del doc["constraints"][0]["expressionAst"]["type"]
        assert code_of(dumps(doc)) == UNKNOWN_NODE_TYPE
        doc = base_doc()
        doc["constraints"][0]["expressionAst"]["type"] = 5
        assert code_of(dumps(doc)) == UNKNOWN_NODE_TYPE

    def test_structural_type_mismatches(self) -> None:
        doc = base_doc()
        doc["decisionVariables"] = {"id": "x"}
        assert code_of(dumps(doc)) == TYPE_MISMATCH
        doc = base_doc()
        doc["timeHorizon"] = [1]
        assert code_of(dumps(doc)) == TYPE_MISMATCH
        doc = base_doc()
        doc["constraints"][0]["expressionAst"] = "x"
        assert code_of(dumps(doc)) == TYPE_MISMATCH


class TestNumbers:
    def test_nan_infinity_constants_rejected(self) -> None:
        for token in ("NaN", "Infinity", "-Infinity"):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
            assert code_of(text) == NON_FINITE_NUMBER

    def test_numeric_overflow_rejected(self) -> None:
        for token in ("1e999", "-1e999", "1.8e308", "1E+400"):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
            assert code_of(text) == NUMBER_OVERFLOW

    def test_largest_finite_float_accepted(self) -> None:
        text = dumps(base_doc()).replace('"boundValue": 5', '"boundValue": 1.7976931348623157e308')
        assert parse_problem_spec_json(text).constraints[0].bound_value == 1.7976931348623157e308

    def test_nonzero_underflow_rejected_zero_accepted(self) -> None:
        for token in ("1e-999", "-1e-400", "4e-325"):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
            assert code_of(text) == NUMBER_UNDERFLOW
        for token in ("0.0", "0e-999", "-0.0"):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
            assert parse_problem_spec_json(text).constraints[0].bound_value == 0.0
        text = dumps(base_doc()).replace('"boundValue": 5', '"boundValue": 5e-324')
        assert parse_problem_spec_json(text).constraints[0].bound_value == 5e-324

    def test_integer_digit_limit(self) -> None:
        doc = base_doc()
        doc["objectives"][0]["expressionAst"] = {
            "type": "literal",
            "dataType": "integer",
            "value": 10**1400,
        }
        assert code_of(dumps(doc)) == INTEGER_TOO_LARGE
        doc["objectives"][0]["expressionAst"]["value"] = 10**1200
        assert parse_problem_spec_json(dumps(doc))

    def test_integer_beyond_2_53_in_m1_float_field_rejected(self) -> None:
        for n in (2**53 + 1, -(2**53) - 1, 10**30):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {n}')
            assert code_of(text) == NUMBER_NOT_EXACT_IN_M1
        text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {2**53}')
        assert parse_problem_spec_json(text).constraints[0].bound_value == float(2**53)

    def test_integer_in_variable_bound_beyond_2_53_rejected(self) -> None:
        doc = base_doc()
        doc["decisionVariables"][0]["upperBound"] = 2**60
        assert code_of(dumps(doc)) == NUMBER_NOT_EXACT_IN_M1

    def test_string_numbers_not_coerced_in_strict_mode(self) -> None:
        for field_text in ('"boundValue": "5"', '"boundValue": "NaN"', '"boundValue": true'):
            text = dumps(base_doc()).replace('"boundValue": 5', field_text)
            assert code_of(text) == M1_VALIDATION_FAILED

    def test_overlong_float_token(self) -> None:
        token = "0." + "0" * 500 + "1"
        text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
        assert code_of(text) == INVALID_JSON

    def test_leading_zero_and_plus_sign_are_invalid_json(self) -> None:
        for token in ("05", "+5", ".5", "5.", "0x10"):
            text = dumps(base_doc()).replace('"boundValue": 5', f'"boundValue": {token}')
            assert code_of(text) == INVALID_JSON


class TestVerificationFlagsNotTrusted:
    def test_client_mathematically_verified_true_rejected(self) -> None:
        doc = base_doc()
        doc["mathematicallyVerified"] = True
        assert code_of(dumps(doc)) == VERIFICATION_FLAG_NOT_TRUSTED

    @pytest.mark.parametrize("value", ["true", 1, "yes", "false", 0, None, [], {}])
    def test_every_non_false_value_rejected_even_when_m1_would_coerce_it(self, value: Any) -> None:
        doc = base_doc()
        doc["mathematicallyVerified"] = value
        assert code_of(dumps(doc)) == VERIFICATION_FLAG_NOT_TRUSTED
        # Frozen M1 silently coerces scalar non-true values to False:
        if value is not None and value not in ([], {}):
            assert GeneralizedProblemSpec.model_validate(doc).mathematically_verified is False

    def test_snake_case_spelling_also_checked(self) -> None:
        doc = base_doc()
        doc["mathematically_verified"] = "true"
        assert code_of(dumps(doc)) == VERIFICATION_FLAG_NOT_TRUSTED

    def test_false_is_accepted(self) -> None:
        doc = base_doc()
        doc["mathematicallyVerified"] = False
        assert parse_problem_spec_json(dumps(doc)).mathematically_verified is False

    def test_client_verification_fields_are_unknown_fields(self) -> None:
        for field in ("verificationClaims", "claims", "verified", "outcome"):
            doc = base_doc()
            doc[field] = []
            assert code_of(dumps(doc)) == UNKNOWN_FIELD


class TestStructureAndEncoding:
    def test_invalid_json(self) -> None:
        for text in ("", "{", '{"a":}', "{'a': 1}", '{"a": 1,}', "nul", '{"a": 1} trailing'):
            assert code_of(text) == INVALID_JSON

    def test_non_object_root(self) -> None:
        for text in ("[]", "1", '"s"', "null", "true"):
            assert code_of(text) == NON_OBJECT_ROOT

    def test_invalid_utf8_and_bom(self) -> None:
        assert code_of(b"\xff\xfe\x00") == INVALID_ENCODING
        assert code_of("﻿" + dumps(base_doc())) == INVALID_JSON
        assert code_of(b"\xef\xbb\xbf" + dumps(base_doc()).encode()) == INVALID_JSON

    def test_lone_surrogates_rejected(self) -> None:
        text = dumps(base_doc()).replace('"title": "t"', '"title": "\\ud800"')
        assert code_of(text) == UNPAIRED_SURROGATE
        text = dumps(base_doc()).replace('"title": "t"', '"title": "ok", "\\udc00": 1')
        assert code_of(text) == UNPAIRED_SURROGATE
        assert code_of('{"title": "\ud800"}') == UNPAIRED_SURROGATE

    def test_valid_surrogate_pair_accepted(self) -> None:
        text = dumps(base_doc()).replace('"title": "t"', '"title": "\\ud83d\\ude00"')
        assert parse_problem_spec_json(text).title == "\U0001f600"

    def test_unsupported_input_type(self) -> None:
        assert code_of(123) == INVALID_JSON  # type: ignore[arg-type]


class TestResourceLimits:
    def test_size_limit_bytes_and_str(self) -> None:
        small = RawIngressLimits(max_bytes=100)
        assert code_of(dumps(base_doc()), small) == INPUT_TOO_LARGE
        assert code_of(dumps(base_doc()).encode(), small) == INPUT_TOO_LARGE
        assert code_of(" " * 2_000_000) == INPUT_TOO_LARGE

    def test_multibyte_size_counted_in_bytes(self) -> None:
        doc = base_doc()
        doc["title"] = "中" * 100  # 300 UTF-8 bytes, 100 characters
        raw = json.dumps(doc, ensure_ascii=False)
        encoded_len = len(raw.encode("utf-8"))
        assert encoded_len > len(raw)
        assert parse_problem_spec_json(raw, RawIngressLimits(max_bytes=encoded_len))
        assert code_of(raw, RawIngressLimits(max_bytes=encoded_len - 1)) == INPUT_TOO_LARGE
        assert code_of(raw.encode(), RawIngressLimits(max_bytes=encoded_len - 1)) == INPUT_TOO_LARGE

    def test_deep_array_nesting(self) -> None:
        assert code_of("[" * 5000 + "]" * 5000) == NESTING_TOO_DEEP
        assert code_of('{"a":' * 70 + "1" + "}" * 70) == NESTING_TOO_DEEP

    def test_brackets_inside_strings_do_not_count(self) -> None:
        doc = base_doc()
        doc["title"] = "[" * 500 + '\\"' + "{" * 500
        assert parse_problem_spec_json(json.dumps(doc)).title.startswith("[")

    def test_depth_boundary(self) -> None:
        text = dumps(base_doc())
        # Deepest nesting: root(1) > constraints(2) > constraint(3) > expressionAst(4)
        assert parse_problem_spec_json(text, RawIngressLimits(max_depth=4))
        assert code_of(text, RawIngressLimits(max_depth=3)) == NESTING_TOO_DEEP

    def test_deep_expression_chain_through_ingress(self) -> None:
        node: dict[str, Any] = {"type": "variable_ref", "variableId": "VAR-01"}
        for _ in range(100):
            node = {
                "type": "binary_op",
                "operator": "add",
                "left": {"type": "variable_ref", "variableId": "VAR-01"},
                "right": node,
            }
        doc = base_doc()
        doc["objectives"][0]["expressionAst"] = node
        assert code_of(dumps(doc)) == NESTING_TOO_DEEP
        assert parse_problem_spec_json(dumps(doc), RawIngressLimits(max_depth=150))

    def test_node_limit(self) -> None:
        assert code_of(dumps(base_doc()), RawIngressLimits(max_nodes=10)) == TOO_MANY_NODES

    def test_array_length_limit(self) -> None:
        doc = base_doc()
        doc["clarificationQuestions"] = ["q"] * 11
        assert code_of(dumps(doc), RawIngressLimits(max_array_len=10)) == ARRAY_TOO_LONG

    def test_object_width_limit(self) -> None:
        doc = base_doc()
        doc["constraints"][0]["expressionAst"]["unit"] = None
        assert code_of(dumps(doc), RawIngressLimits(max_object_keys=2)) == OBJECT_TOO_WIDE

    def test_string_length_limit(self) -> None:
        doc = base_doc()
        doc["title"] = "x" * 101
        assert code_of(dumps(doc), RawIngressLimits(max_string_chars=100)) == STRING_TOO_LONG

    def test_limits_validate_depth_range(self) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            RawIngressLimits(max_depth=0)
        with pytest.raises(ValueError, match="max_depth"):
            RawIngressLimits(max_depth=10_000)


class TestGuaranteeScope:
    def test_m1_alone_is_permissive_so_guarantee_requires_the_scanner(self) -> None:
        doc = base_doc()
        doc["decisionVariables"][0]["upperBoud"] = 99
        doc["decisionVariables"][0]["upper_bound"] = 1
        m1 = GeneralizedProblemSpec.model_validate(copy.deepcopy(doc))
        # M1 accepted an unknown key and a conflicting alias without complaint.
        assert m1.decision_variables[0].upper_bound == 10.0
        with pytest.raises(RawIngressError):
            parse_problem_spec_json(dumps(doc))
