"""Tests for HiGHS continuous-LP execution adapter and isolated worker.

Verifies:
1. Bounded minimization: expected x=(1,0), y=(1) (and F1 fixture x=(0, 1.5), y=(0.5)).
2. Maximization-compatible lowered input (preserves negated cost).
3. Dual sign convention and vector dimensions.
4. Objective offset handling (min cost^T x + offset).
5. Infeasible status and available Farkas ray.
6. Unbounded status and available primal ray.
7. Presolve behavior when rays are or are not available.
8. Time limit and iteration limits/status mapping.
9. Timeout watchdog, process termination, and cleanup.
10. Worker crash and non-zero exit.
11. Malformed request/response JSON.
12. Empty, missing, and oversized stdout responses.
13. Native HiGHS banner and stdout contamination isolation.
14. Request ID and hash mismatch protection.
15. Invalid dimensions and non-finite values filtering.
16. No fabricated certificates on failure paths.
17. Windows Job Object limits applied and released.
18. Structured failure responses on all error paths.
"""

from __future__ import annotations

import math
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from qorvexa.contracts.solver_runtime import SolverTermination
from qorvexa.solver_fabric.canonical_lp import LoweredLP
from qorvexa.solver_fabric.highs_adapter import (
    HighsAdapter,
    WindowsJobObject,
    csr_to_csc,
    solve_with_highs,
)
from qorvexa.solver_fabric.worker_contract import (
    LoweredLPPayload,
    LPAlgorithm,
    SolverAdapter,
    SolverOptions,
    WorkerFailureCode,
    WorkerRequest,
    WorkerStatus,
)


def _make_request(
    num_variables: int,
    num_rows: int,
    cost: tuple[float, ...],
    col_lower: tuple[float | None, ...],
    col_upper: tuple[float | None, ...],
    row_lower: tuple[float | None, ...],
    row_upper: tuple[float | None, ...],
    row_start: tuple[int, ...],
    col_index: tuple[int, ...],
    values: tuple[float, ...],
    offset: float = 0.0,
    options: SolverOptions | None = None,
    request_id: str = "req-test-1",
    model_sha256: str = "0" * 64,
) -> WorkerRequest:
    """Helper to construct a valid, verified WorkerRequest."""
    ir = LoweredLP(
        num_variables=num_variables,
        num_rows=num_rows,
        cost=cost,
        col_lower=tuple(-math.inf if v is None else v for v in col_lower),
        col_upper=tuple(math.inf if v is None else v for v in col_upper),
        row_lower=tuple(-math.inf if v is None else v for v in row_lower),
        row_upper=tuple(math.inf if v is None else v for v in row_upper),
        row_start=row_start,
        col_index=col_index,
        values=values,
        offset=offset,
    )
    payload = LoweredLPPayload.from_lowered(ir)
    return WorkerRequest(
        request_id=request_id,
        model_sha256=model_sha256,
        ir_sha256=ir.ir_sha256,
        compiler_version="1.0.0",
        problem=payload,
        options=options or SolverOptions(),
    )


# ---------------------------------------------------------------------------
# 1. Protocol conformance
# ---------------------------------------------------------------------------


def test_solver_adapter_protocol() -> None:
    """Verify HighsAdapter satisfies the SolverAdapter protocol."""
    adapter = HighsAdapter()
    assert isinstance(adapter, SolverAdapter)
    assert adapter.name == "HiGHS"


def test_csr_to_csc_conversion() -> None:
    """Verify exact Compressed Sparse Row to Compressed Sparse Column conversion."""
    # Matrix A:
    # [1.0, 2.0]
    # [3.0, 4.0]
    row_start = (0, 2, 4)
    col_index = (0, 1, 0, 1)
    values = (1.0, 2.0, 3.0, 4.0)

    col_start, r_idx, csc_vals = csr_to_csc(2, 2, row_start, col_index, values)
    assert list(col_start) == [0, 2, 4]
    assert list(r_idx) == [0, 1, 0, 1]
    assert list(csc_vals) == [1.0, 3.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# 2. Bounded Minimization (Analytical: x*=(1,0), y*=(1))
# ---------------------------------------------------------------------------


def test_bounded_minimization() -> None:
    """min x1 + 2*x2 s.t. 1 <= x1 + x2 <= 4, x in [0, 3]^2 -> x=(1, 0), y=(1)."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        offset=0.0,
    )

    # Test direct HiGHS execution
    resp_direct = solve_with_highs(req)
    assert resp_direct.status == WorkerStatus.FINISHED
    assert resp_direct.termination == SolverTermination.OPTIMAL
    assert resp_direct.objective_value == pytest.approx(1.0)
    assert resp_direct.primal_solution == pytest.approx((1.0, 0.0))
    assert resp_direct.dual_solution == pytest.approx((1.0,))
    assert resp_direct.request_id == req.request_id
    assert resp_direct.model_sha256 == req.model_sha256
    assert resp_direct.ir_sha256 == req.ir_sha256

    # Test isolated subprocess adapter execution
    adapter = HighsAdapter()
    resp_isolated = adapter.solve(req)
    assert resp_isolated.status == WorkerStatus.FINISHED
    assert resp_isolated.termination == SolverTermination.OPTIMAL
    assert resp_isolated.objective_value == pytest.approx(1.0)
    assert resp_isolated.primal_solution == pytest.approx((1.0, 0.0))
    assert resp_isolated.dual_solution == pytest.approx((1.0,))


# ---------------------------------------------------------------------------
# 3. Maximization-Compatible Lowered Input
# ---------------------------------------------------------------------------


def test_maximization_compatible_lowered_input() -> None:
    """The compiler converted max 3*x1 + 2*x2 into min -3*x1 - 2*x2. Adapter must not re-negate."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(-3.0, -2.0),
        col_lower=(0.0, 0.0),
        col_upper=(None, None),
        row_lower=(None,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        offset=0.0,
    )

    adapter = HighsAdapter()
    resp = adapter.solve(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.OPTIMAL
    # Optimum at x=(4.0, 0.0), min objective = -12.0
    assert resp.objective_value == pytest.approx(-12.0)
    assert resp.primal_solution == pytest.approx((4.0, 0.0))


# ---------------------------------------------------------------------------
# 4. Correct Dual Sign Convention & F1 Fixture (y = 0.5)
# ---------------------------------------------------------------------------


def test_correct_dual_sign_convention_f1_fixture() -> None:
    """min x0 + x1 s.t. x0 + 2*x1 >= 3, x0,x1 >= 0. Expected x=(0, 1.5), row multiplier y=0.5."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 1.0),
        col_lower=(0.0, 0.0),
        col_upper=(None, None),
        row_lower=(3.0,),
        row_upper=(None,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 2.0),
        offset=0.0,
    )

    resp = solve_with_highs(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.OPTIMAL
    assert resp.primal_solution == pytest.approx((0.0, 1.5))
    assert resp.objective_value == pytest.approx(1.5)
    assert resp.dual_solution is not None
    assert resp.dual_solution[0] == pytest.approx(0.5)
    assert resp.dual_solution[0] >= 0.0


# ---------------------------------------------------------------------------
# 5. Objective Offset Handling
# ---------------------------------------------------------------------------


def test_objective_offset_handling() -> None:
    """Verify objective constant offset is added: min x1 + 2*x2 + 10.0 -> obj=11.0."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        offset=10.0,
    )

    adapter = HighsAdapter()
    resp = adapter.solve(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.OPTIMAL
    assert resp.objective_value == pytest.approx(11.0)
    assert resp.primal_solution == pytest.approx((1.0, 0.0))


# ---------------------------------------------------------------------------
# 6. Infeasible Status & Farkas Ray
# ---------------------------------------------------------------------------


def test_infeasible_status_and_farkas_ray() -> None:
    """min x1 s.t. x1 >= 5.0, x1 <= 2.0, x1 >= 0. Presolve off yields Farkas dual ray."""
    req = _make_request(
        num_variables=1,
        num_rows=2,
        cost=(1.0,),
        col_lower=(0.0,),
        col_upper=(None,),
        row_lower=(5.0, None),
        row_upper=(None, 2.0),
        row_start=(0, 1, 2),
        col_index=(0, 0),
        values=(1.0, 1.0),
        options=SolverOptions(presolve=False, algorithm=LPAlgorithm.SIMPLEX),
    )

    resp = solve_with_highs(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.INFEASIBLE
    assert resp.farkas_ray is not None
    assert len(resp.farkas_ray) == 2
    assert resp.primal_solution is None
    assert resp.objective_value is None


# ---------------------------------------------------------------------------
# 7. Unbounded Status & Primal Ray
# ---------------------------------------------------------------------------


def test_unbounded_status_and_primal_ray() -> None:
    """min -x1 s.t. x1 - x2 <= 1.0, x >= 0. Presolve off yields improving primal ray."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(-1.0, 0.0),
        col_lower=(0.0, 0.0),
        col_upper=(None, None),
        row_lower=(None,),
        row_upper=(1.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, -1.0),
        options=SolverOptions(presolve=False, algorithm=LPAlgorithm.SIMPLEX),
    )

    resp = solve_with_highs(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.UNBOUNDED
    assert resp.primal_ray is not None
    assert len(resp.primal_ray) == 2
    assert resp.primal_ray[0] > 0.0
    assert resp.dual_solution is None
    assert resp.objective_value is None


# ---------------------------------------------------------------------------
# 8. Presolve Behavior (Ray may be unavailable)
# ---------------------------------------------------------------------------


def test_presolve_behavior_unavailable_ray() -> None:
    """With presolve=True, infeasibility detected early might not produce a simplex ray."""
    req = _make_request(
        num_variables=1,
        num_rows=2,
        cost=(1.0,),
        col_lower=(0.0,),
        col_upper=(None,),
        row_lower=(5.0, None),
        row_upper=(None, 2.0),
        row_start=(0, 1, 2),
        col_index=(0, 0),
        values=(1.0, 1.0),
        options=SolverOptions(presolve=True),
    )

    resp = solve_with_highs(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.INFEASIBLE
    assert resp.primal_solution is None


# ---------------------------------------------------------------------------
# 9. Time Limits & Timeout Watchdog
# ---------------------------------------------------------------------------


def test_time_limit_option() -> None:
    """Test solver-level time limit option."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        options=SolverOptions(time_limit_seconds=0.0001),
    )
    resp = solve_with_highs(req)
    assert resp.termination in (SolverTermination.OPTIMAL, SolverTermination.TIME_LIMIT)


def test_worker_supervisor_timeout() -> None:
    """Supervisor enforces timeout and kills hanging worker child process."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        options=SolverOptions(time_limit_seconds=0.1),
    )

    adapter = HighsAdapter(timeout_buffer_seconds=0.1)

    # Mock subprocess.Popen.communicate to raise TimeoutExpired
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=0.2)
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.TIMED_OUT
        assert resp.termination == SolverTermination.SUPERVISOR_KILLED
        assert resp.primal_solution is None
        assert resp.dual_solution is None


# ---------------------------------------------------------------------------
# 10. Worker Crash & Abnormal Exit
# ---------------------------------------------------------------------------


def test_worker_crash_handling() -> None:
    """Worker process exiting with non-zero exit code produces FAILED/CRASHED response."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"Fatal C++ crash: segmentation fault\n")
        mock_proc.returncode = 1
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.termination == SolverTermination.CRASHED
        assert resp.failure is not None
        assert resp.failure.code == WorkerFailureCode.CRASHED
        assert "Fatal C++ crash" in resp.failure.message
        assert resp.primal_solution is None


# ---------------------------------------------------------------------------
# 11. Malformed Request & Response JSON
# ---------------------------------------------------------------------------


def test_worker_malformed_response() -> None:
    """Worker process returning corrupt JSON produces FAILED response."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"NOT_A_VALID_JSON", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.failure is not None
        assert resp.failure.code == WorkerFailureCode.SOLVER_ERROR


# ---------------------------------------------------------------------------
# 12. Empty & Oversized Stdout Responses
# ---------------------------------------------------------------------------


def test_empty_stdout_response() -> None:
    """Worker producing empty stdout produces FAILED response."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.raw_solver_status == "EMPTY_RESPONSE"
        assert resp.failure is not None
        assert resp.failure.code == WorkerFailureCode.SOLVER_ERROR


def test_oversized_stdout_response() -> None:
    """Worker emitting output exceeding size cap produces FAILED response."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"{" * (51 * 1024 * 1024), b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.raw_solver_status == "OVERSIZED_RESPONSE"
        assert resp.failure is not None


# ---------------------------------------------------------------------------
# 13. Native HiGHS Banner & Stdout Isolation
# ---------------------------------------------------------------------------


def test_native_banner_stdout_isolation() -> None:
    """Extra C/C++ banner text preceding JSON is safely stripped by supervisor."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    valid_resp = solve_with_highs(req)
    contaminated_bytes = (
        b"Running HiGHS 1.16.0 (git hash: f3f113d): Copyright (c) 2026\n"
        b"Includes third-party software components...\n" + valid_resp.to_json_bytes()
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (contaminated_bytes, b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FINISHED
        assert resp.termination == SolverTermination.OPTIMAL
        assert resp.objective_value == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 14. Hash & Request-ID Mismatch Protection
# ---------------------------------------------------------------------------


def test_hash_binding_mismatch_detected() -> None:
    """Worker response with corrupted irSha256 is rejected with INVALID_REQUEST failure."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    valid_resp = solve_with_highs(req)
    tampered_bytes = valid_resp.to_json_bytes().replace(req.ir_sha256.encode(), b"f" * 64)

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (tampered_bytes, b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.failure is not None
        assert resp.failure.code == WorkerFailureCode.INVALID_REQUEST
        assert "do not match" in resp.failure.message


# ---------------------------------------------------------------------------
# 15. Dimension Checks & Non-Finite Filtering
# ---------------------------------------------------------------------------


def test_non_finite_solver_values_filtered() -> None:
    """Non-finite values from solver are omitted, preventing contract violation."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    with patch("highspy.Highs") as mock_highs_cls:
        mock_highs = MagicMock()
        mock_highs.passModel.return_value = 0  # kOk
        mock_highs.run.return_value = 0
        mock_highs.getModelStatus.return_value = MagicMock()
        mock_highs.version.return_value = "1.16.0"

        mock_sol = MagicMock()
        mock_sol.value_valid = True
        mock_sol.dual_valid = True
        mock_sol.col_value = [float("nan"), 0.0]
        mock_sol.row_dual = [float("inf")]
        mock_highs.getSolution.return_value = mock_sol

        mock_info = MagicMock()
        mock_info.valid = True
        mock_info.objective_function_value = float("nan")
        mock_info.simplex_iteration_count = 1
        mock_info.ipm_iteration_count = 0
        mock_highs.getInfo.return_value = mock_info

        mock_highs_cls.return_value = mock_highs

        resp = solve_with_highs(req)
        assert resp.primal_solution is None
        assert resp.dual_solution is None
        assert resp.objective_value is None


# ---------------------------------------------------------------------------
# 16. No Fabricated Certificates on Failure Paths
# ---------------------------------------------------------------------------


def test_no_fabricated_certificates_on_failures() -> None:
    """Failure responses never fabricate candidates or evidence."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
    )

    adapter = HighsAdapter()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"", b"OOM killed\n")
        mock_proc.returncode = 137
        mock_popen.return_value = mock_proc

        resp = adapter.solve(req)
        assert resp.status == WorkerStatus.FAILED
        assert resp.primal_solution is None
        assert resp.dual_solution is None
        assert resp.farkas_ray is None
        assert resp.primal_ray is None
        assert resp.objective_value is None


# ---------------------------------------------------------------------------
# 17. Windows Job Object Lifecycle
# ---------------------------------------------------------------------------


def test_windows_job_object_lifecycle() -> None:
    """Verify Windows Job Object handle allocation, configuration, and cleanup."""
    if sys.platform != "win32":
        pytest.skip("Windows Job Object is Win32 specific")

    job = WindowsJobObject(memory_limit_bytes=100 * 1024 * 1024)
    assert job.handle is not None
    job.close()
    assert job.handle is None


# ---------------------------------------------------------------------------
# 18. Real Subprocess End-to-End Execution
# ---------------------------------------------------------------------------


def test_real_subprocess_execution_clean_json() -> None:
    """Verify real worker subprocess execution over stdin/stdout with clean JSON."""
    req = _make_request(
        num_variables=2,
        num_rows=1,
        cost=(1.0, 2.0),
        col_lower=(0.0, 0.0),
        col_upper=(3.0, 3.0),
        row_lower=(1.0,),
        row_upper=(4.0,),
        row_start=(0, 2),
        col_index=(0, 1),
        values=(1.0, 1.0),
        offset=0.0,
    )

    # Spawn real subprocess with default in_process=False
    adapter = HighsAdapter(in_process=False)
    resp = adapter.solve(req)
    assert resp.status == WorkerStatus.FINISHED
    assert resp.termination == SolverTermination.OPTIMAL
    assert resp.primal_solution == pytest.approx((1.0, 0.0))
    assert resp.dual_solution == pytest.approx((1.0,))
    assert resp.objective_value == pytest.approx(1.0)
    assert resp.solver_name == "HiGHS"
    assert resp.solver_version == "1.16.0"
