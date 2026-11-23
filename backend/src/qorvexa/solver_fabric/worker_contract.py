"""Stable solver-worker contract (published for the isolated adapter implementer).

CONTRACT_VERSION 1. Do not change casually: any change bumps CONTRACT_VERSION.

Roles
-----
* The orchestrator builds a `WorkerRequest` from the compiled IR and hands it to a
  `SolverAdapter`. The adapter solves it in isolation and returns a `WorkerResponse`.
* A response carries RAW solver output only. Nothing in it is evidence: status, objective,
  vectors and rays are candidates that the independent verifier checks exactly. Solver status
  never decides a mathematical outcome.

Conventions the adapter MUST follow
-----------------------------------
* The problem is the minimization form  min cost^T x + offset  s.t.  row_lower <= A x <= row_upper,
  col_lower <= x <= col_upper  (maximization was already negated by the compiler). Vectors are in
  the IR's index order: primal/ray indexed by column, dual/farkas by row.
* `dual_solution` is y with reduced costs r = cost - A^T y (so a row whose lower bound is active
  in a minimization typically has y >= 0). Any y is safe: the verifier uses weak duality.
* `farkas_ray` (row space) should satisfy: y^T A moves against the box. It is only a candidate.
* `primal_ray` (column space) is a recession direction candidate (improving: cost^T d < 0).
* Bounds travel as `null` for "unbounded on that side" (JSON has no infinity); all numbers finite.
* The adapter must NOT raise for solver outcomes. Any internal failure is returned as
  status FAILED with a `WorkerFailure`. A time limit is enforced by the adapter/supervisor and is
  returned as status TIMED_OUT (termination TIME_LIMIT, or SUPERVISOR_KILLED if killed).
* The response echoes request_id, model_sha256 and ir_sha256 unchanged; the orchestrator rejects
  any mismatch.

Suggested HiGHS status -> `SolverTermination` mapping (metadata only):
  kOptimal->OPTIMAL, kInfeasible->INFEASIBLE, kUnbounded->UNBOUNDED,
  kUnboundedOrInfeasible->UNBOUNDED_OR_INFEASIBLE, kTimeLimit->TIME_LIMIT,
  kIterationLimit->ITERATION_LIMIT, kSolveError/kModelError->NUMERICAL_ERROR (or OTHER),
  kInterrupt->INTERRUPTED, anything else->OTHER.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from qorvexa.contracts.solver_runtime import Sha256Hex, SolverTermination
from qorvexa.solver_fabric.canonical_lp import LoweredLP

CONTRACT_VERSION: Final = 1
MAX_RAW_TEXT: Final = 2000

FiniteNumber = Annotated[float, Field(allow_inf_nan=False)]


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, alias_generator=to_camel
    )


class LPAlgorithm(StrEnum):
    AUTO = "AUTO"
    SIMPLEX = "SIMPLEX"
    IPM = "IPM"


class SolverOptions(_Frozen):
    """Fixed, typed options. There is deliberately no free-form parameter map."""

    time_limit_seconds: float = Field(default=60.0, gt=0, le=86400, alias="timeLimitSeconds")
    threads: int = Field(default=1, ge=1, le=256)
    algorithm: LPAlgorithm = LPAlgorithm.AUTO
    presolve: bool = True
    primal_feasibility_tolerance: float = Field(
        default=1e-7, gt=0, le=1e-2, alias="primalFeasibilityTolerance"
    )
    dual_feasibility_tolerance: float = Field(
        default=1e-7, gt=0, le=1e-2, alias="dualFeasibilityTolerance"
    )
    random_seed: int = Field(default=0, ge=0, le=2**31 - 1, alias="randomSeed")


class LoweredLPPayload(_Frozen):
    """Wire form of the compiled IR (CSR). `None` bound = unbounded on that side."""

    num_variables: int = Field(..., ge=0, alias="numVariables")
    num_rows: int = Field(..., ge=0, alias="numRows")
    cost: tuple[FiniteNumber, ...]
    col_lower: tuple[FiniteNumber | None, ...] = Field(..., alias="colLower")
    col_upper: tuple[FiniteNumber | None, ...] = Field(..., alias="colUpper")
    row_lower: tuple[FiniteNumber | None, ...] = Field(..., alias="rowLower")
    row_upper: tuple[FiniteNumber | None, ...] = Field(..., alias="rowUpper")
    row_start: tuple[int, ...] = Field(..., alias="rowStart")
    col_index: tuple[int, ...] = Field(..., alias="colIndex")
    values: tuple[FiniteNumber, ...]
    offset: FiniteNumber

    @model_validator(mode="after")
    def _shape(self) -> LoweredLPPayload:
        n, m = self.num_variables, self.num_rows
        if not (len(self.cost) == len(self.col_lower) == len(self.col_upper) == n):
            raise ValueError("column vectors must have numVariables entries")
        if not (len(self.row_lower) == len(self.row_upper) == m):
            raise ValueError("row vectors must have numRows entries")
        if len(self.row_start) != m + 1 or self.row_start[0] != 0:
            raise ValueError("rowStart must have numRows+1 entries starting at 0")
        if any(b < a for a, b in zip(self.row_start, self.row_start[1:], strict=False)):
            raise ValueError("rowStart must be non-decreasing")
        if self.row_start[-1] != len(self.values) or len(self.col_index) != len(self.values):
            raise ValueError("CSR arrays are inconsistent")
        if any(not 0 <= j < n for j in self.col_index):
            raise ValueError("colIndex out of range")
        return self

    @classmethod
    def from_lowered(cls, ir: LoweredLP) -> LoweredLPPayload:
        def fin(v: float, inf: float) -> float | None:
            return None if v == inf else v

        return cls(
            num_variables=ir.num_variables,
            num_rows=ir.num_rows,
            cost=ir.cost,
            col_lower=tuple(fin(v, -math.inf) for v in ir.col_lower),
            col_upper=tuple(fin(v, math.inf) for v in ir.col_upper),
            row_lower=tuple(fin(v, -math.inf) for v in ir.row_lower),
            row_upper=tuple(fin(v, math.inf) for v in ir.row_upper),
            row_start=ir.row_start,
            col_index=ir.col_index,
            values=ir.values,
            offset=ir.offset,
        )

    def to_lowered(self) -> LoweredLP:
        def inf(v: float | None, default: float) -> float:
            return default if v is None else v

        return LoweredLP(
            num_variables=self.num_variables,
            num_rows=self.num_rows,
            cost=self.cost,
            col_lower=tuple(inf(v, -math.inf) for v in self.col_lower),
            col_upper=tuple(inf(v, math.inf) for v in self.col_upper),
            row_lower=tuple(inf(v, -math.inf) for v in self.row_lower),
            row_upper=tuple(inf(v, math.inf) for v in self.row_upper),
            row_start=self.row_start,
            col_index=self.col_index,
            values=self.values,
            offset=self.offset,
        )


class WorkerRequest(_Frozen):
    """Immutable solve request, bound to the exact model and IR by SHA-256."""

    contract_version: int = Field(default=CONTRACT_VERSION, alias="contractVersion")
    request_id: str = Field(..., min_length=1, max_length=128, alias="requestId")
    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    ir_sha256: Sha256Hex = Field(..., alias="irSha256")
    compiler_version: str = Field(..., min_length=1, max_length=64, alias="compilerVersion")
    problem: LoweredLPPayload
    options: SolverOptions = Field(default_factory=SolverOptions)

    @model_validator(mode="after")
    def _bind(self) -> WorkerRequest:
        if self.contract_version != CONTRACT_VERSION:
            raise ValueError(f"unsupported contractVersion {self.contract_version}")
        if self.problem.to_lowered().ir_sha256 != self.ir_sha256:
            raise ValueError("irSha256 does not match the problem payload")
        return self

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> WorkerRequest:
        return cls.model_validate_json(data, strict=True)


class WorkerStatus(StrEnum):
    FINISHED = "FINISHED"  # the solver ran to its own termination (any termination)
    TIMED_OUT = "TIMED_OUT"  # wall-clock limit hit / supervisor kill
    FAILED = "FAILED"  # worker or solver failure; see `failure`


class WorkerFailureCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    SOLVER_ERROR = "SOLVER_ERROR"
    CRASHED = "CRASHED"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    UNAVAILABLE = "UNAVAILABLE"
    OTHER = "OTHER"


class WorkerFailure(_Frozen):
    code: WorkerFailureCode
    message: str = Field(..., min_length=1, max_length=MAX_RAW_TEXT)


class WorkerResponse(_Frozen):
    """RAW solver output. Never evidence; every field is a candidate or metadata."""

    contract_version: int = Field(default=CONTRACT_VERSION, alias="contractVersion")
    request_id: str = Field(..., min_length=1, max_length=128, alias="requestId")
    model_sha256: Sha256Hex = Field(..., alias="modelSha256")
    ir_sha256: Sha256Hex = Field(..., alias="irSha256")
    status: WorkerStatus
    termination: SolverTermination
    raw_solver_status: str = Field(default="", max_length=256, alias="rawSolverStatus")
    solver_name: str = Field(default="", max_length=64, alias="solverName")
    solver_version: str = Field(default="", max_length=64, alias="solverVersion")
    objective_value: FiniteNumber | None = Field(default=None, alias="objectiveValue")
    primal_solution: tuple[FiniteNumber, ...] | None = Field(default=None, alias="primalSolution")
    dual_solution: tuple[FiniteNumber, ...] | None = Field(default=None, alias="dualSolution")
    farkas_ray: tuple[FiniteNumber, ...] | None = Field(default=None, alias="farkasRay")
    primal_ray: tuple[FiniteNumber, ...] | None = Field(default=None, alias="primalRay")
    iterations: int | None = Field(default=None, ge=0)
    runtime_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False, alias="runtimeSeconds")
    diagnostics: dict[str, str] = Field(default_factory=dict)
    failure: WorkerFailure | None = None

    @model_validator(mode="after")
    def _consistent(self) -> WorkerResponse:
        if self.status == WorkerStatus.FAILED and self.failure is None:
            raise ValueError("status FAILED requires a failure")
        if self.status != WorkerStatus.FAILED and self.failure is not None:
            raise ValueError("failure is only valid with status FAILED")
        if self.status == WorkerStatus.TIMED_OUT and self.termination not in (
            SolverTermination.TIME_LIMIT,
            SolverTermination.SUPERVISOR_KILLED,
        ):
            raise ValueError("TIMED_OUT requires termination TIME_LIMIT or SUPERVISOR_KILLED")
        if len(self.diagnostics) > 50 or any(
            len(k) > 64 or len(v) > MAX_RAW_TEXT for k, v in self.diagnostics.items()
        ):
            raise ValueError("diagnostics exceed size limits")
        return self

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> WorkerResponse:
        return cls.model_validate_json(data, strict=True)


@runtime_checkable
class SolverAdapter(Protocol):
    """What the orchestrator consumes. Implementations live outside this module."""

    @property
    def name(self) -> str: ...

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        """Solve in isolation and return RAW output. Must not raise for solver outcomes."""
        ...
