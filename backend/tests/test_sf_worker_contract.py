"""Worker contract tests: serialization, hash binding, validators, protocol."""

from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from qorvexa.contracts.solver_runtime import SolverTermination
from qorvexa.solver_fabric.canonical_lp import (
    ColumnSpec,
    RowSpec,
    build_canonical_lp,
    lower_ranged_lp,
)
from qorvexa.solver_fabric.worker_contract import (
    CONTRACT_VERSION,
    LoweredLPPayload,
    SolverAdapter,
    SolverOptions,
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

HASH = "a" * 64


def make_request(**over: Any) -> WorkerRequest:
    m = build_canonical_lp(
        [ColumnSpec("x", Fraction(0), None), ColumnSpec("y", None, Fraction(3))],
        objective_cost={"x": Fraction(1), "y": Fraction(-2)},
        objective_constant=Fraction(4),
        objective_sense="minimize",
        rows=[RowSpec("r", {"x": Fraction(1), "y": Fraction(1, 3)}, None, Fraction(5))],
    )
    ir, rep = lower_ranged_lp(m)
    kw: dict[str, Any] = {
        "request_id": "job-1",
        "model_sha256": m.model_sha256,
        "ir_sha256": ir.ir_sha256,
        "compiler_version": rep.compiler_version,
        "problem": LoweredLPPayload.from_lowered(ir),
    }
    kw.update(over)
    return WorkerRequest(**kw)


def make_response(**over: Any) -> WorkerResponse:
    kw: dict[str, Any] = {
        "request_id": "job-1",
        "model_sha256": HASH,
        "ir_sha256": HASH,
        "status": WorkerStatus.FINISHED,
        "termination": SolverTermination.OPTIMAL,
    }
    kw.update(over)
    return WorkerResponse(**kw)


class TestRequest:
    def test_json_roundtrip_is_lossless(self) -> None:
        req = make_request()
        again = WorkerRequest.from_json_bytes(req.to_json_bytes())
        assert again == req
        assert again.problem.to_lowered().ir_sha256 == req.ir_sha256

    def test_wire_uses_null_for_infinite_bounds_and_camel_case(self) -> None:
        doc = json.loads(make_request().to_json_bytes())
        assert doc["problem"]["colUpper"] == [None, 3.0]
        assert doc["problem"]["colLower"] == [0.0, None]
        assert doc["problem"]["rowLower"] == [None]
        assert doc["contractVersion"] == CONTRACT_VERSION and "modelSha256" in doc

    def test_payload_roundtrips_infinities(self) -> None:
        req = make_request()
        ir = req.problem.to_lowered()
        assert ir.col_upper[0] == math.inf and ir.col_lower[1] == -math.inf
        assert LoweredLPPayload.from_lowered(ir) == req.problem

    def test_tampered_payload_breaks_hash_binding(self) -> None:
        req = make_request()
        doc = json.loads(req.to_json_bytes())
        doc["problem"]["cost"][0] = 9.0
        with pytest.raises(ValidationError, match="irSha256"):
            WorkerRequest.from_json_bytes(json.dumps(doc).encode())

    def test_wrong_contract_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_request(contract_version=2)

    def test_unknown_fields_rejected(self) -> None:
        doc = json.loads(make_request().to_json_bytes())
        doc["extra"] = 1
        with pytest.raises(ValidationError):
            WorkerRequest.from_json_bytes(json.dumps(doc).encode())

    def test_non_finite_numbers_rejected(self) -> None:
        text = make_request().to_json_bytes().decode().replace('"offset":4.0', '"offset":NaN')
        with pytest.raises(ValidationError):
            WorkerRequest.from_json_bytes(text.encode())

    def test_csr_shape_validation(self) -> None:
        base = json.loads(make_request().to_json_bytes())["problem"]
        for key, value in (
            ("rowStart", [0, 1, 2]),
            ("colIndex", [0, 7]),
            ("cost", [1.0]),
            ("values", [1.0]),
        ):
            bad = {**base, key: value}
            with pytest.raises(ValidationError):
                LoweredLPPayload.model_validate(bad)

    def test_no_free_form_options(self) -> None:
        with pytest.raises(ValidationError):
            SolverOptions(custom_parameters={"a": "b"})  # type: ignore[call-arg]
        for bad in ({"time_limit_seconds": 0}, {"threads": 0}, {"algorithm": "magic"}):
            with pytest.raises(ValidationError):
                SolverOptions(**bad)


class TestResponse:
    def test_roundtrip(self) -> None:
        r = make_response(
            primal_solution=(1.0, 2.0), dual_solution=(0.5,), iterations=7, runtime_seconds=0.25
        )
        assert WorkerResponse.from_json_bytes(r.to_json_bytes()) == r

    def test_failed_requires_failure(self) -> None:
        with pytest.raises(ValidationError):
            make_response(status=WorkerStatus.FAILED)
        ok = make_response(
            status=WorkerStatus.FAILED,
            termination=SolverTermination.CRASHED,
            failure=WorkerFailure(code=WorkerFailureCode.CRASHED, message="boom"),
        )
        assert ok.failure is not None

    def test_failure_only_with_failed(self) -> None:
        with pytest.raises(ValidationError):
            make_response(failure=WorkerFailure(code=WorkerFailureCode.OTHER, message="x"))

    def test_timed_out_termination(self) -> None:
        with pytest.raises(ValidationError):
            make_response(status=WorkerStatus.TIMED_OUT, termination=SolverTermination.OPTIMAL)
        for t in (SolverTermination.TIME_LIMIT, SolverTermination.SUPERVISOR_KILLED):
            assert make_response(status=WorkerStatus.TIMED_OUT, termination=t)

    def test_non_finite_vectors_rejected(self) -> None:
        for bad in (math.nan, math.inf):
            with pytest.raises(ValidationError):
                make_response(primal_solution=(bad,))

    def test_bad_hash_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_response(model_sha256="xyz")

    def test_diagnostics_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_response(diagnostics={f"k{i}": "v" for i in range(51)})


class TestAdapterProtocol:
    def test_structural_conformance(self) -> None:
        class Good:
            name = "good"

            def solve(self, request: WorkerRequest) -> WorkerResponse:
                raise NotImplementedError

        class Bad:
            name = "bad"

        assert isinstance(Good(), SolverAdapter)
        assert not isinstance(Bad(), SolverAdapter)
