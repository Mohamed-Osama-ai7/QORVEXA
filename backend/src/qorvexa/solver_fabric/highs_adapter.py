"""HiGHS continuous-LP execution adapter and isolated subprocess supervisor.

Satisfies `SolverAdapter` defined in `qorvexa.solver_fabric.worker_contract`.
Runs native HiGHS (highspy 1.16.0) inside an isolated child process bounded by
a Windows Job Object, enforcing timeouts, thread limits, memory limits, and clean cleanup.
"""

from __future__ import annotations

import contextlib
import math
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from qorvexa.contracts.solver_runtime import SolverTermination
from qorvexa.solver_fabric.worker_contract import (
    MAX_RAW_TEXT,
    LPAlgorithm,
    SolverAdapter,
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # 50 MB response size cap


def csr_to_csc(
    num_variables: int,
    num_rows: int,
    row_start: Sequence[int],
    col_index: Sequence[int],
    values: Sequence[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert Compressed Sparse Row (CSR) arrays to Compressed Sparse Column (CSC) arrays.

    Returns (col_start, row_index, csc_values).
    """
    col_counts = [0] * num_variables
    for c in col_index:
        col_counts[c] += 1

    col_start = np.zeros(num_variables + 1, dtype=np.int32)
    for j in range(num_variables):
        col_start[j + 1] = col_start[j] + col_counts[j]

    col_next = col_start.copy()
    nnz = len(values)
    row_index = np.zeros(nnz, dtype=np.int32)
    csc_values = np.zeros(nnz, dtype=np.float64)

    for i in range(num_rows):
        for p in range(row_start[i], row_start[i + 1]):
            j = col_index[p]
            dest = col_next[j]
            row_index[dest] = i
            csc_values[dest] = values[p]
            col_next[j] += 1

    return col_start, row_index, csc_values


def _map_highs_model_status(model_status: Any) -> SolverTermination:
    """Map highspy HighsModelStatus to SolverTermination enum."""
    import highspy

    if model_status == highspy.HighsModelStatus.kOptimal:
        return SolverTermination.OPTIMAL
    if model_status == highspy.HighsModelStatus.kInfeasible:
        return SolverTermination.INFEASIBLE
    if model_status == highspy.HighsModelStatus.kUnbounded:
        return SolverTermination.UNBOUNDED
    if model_status == highspy.HighsModelStatus.kUnboundedOrInfeasible:
        return SolverTermination.UNBOUNDED_OR_INFEASIBLE
    if model_status == highspy.HighsModelStatus.kTimeLimit:
        return SolverTermination.TIME_LIMIT
    if model_status == highspy.HighsModelStatus.kIterationLimit:
        return SolverTermination.ITERATION_LIMIT
    if model_status in (
        highspy.HighsModelStatus.kSolveError,
        highspy.HighsModelStatus.kModelError,
        highspy.HighsModelStatus.kPresolveError,
        highspy.HighsModelStatus.kPostsolveError,
        highspy.HighsModelStatus.kLoadError,
    ):
        return SolverTermination.NUMERICAL_ERROR

    interrupt_statuses = {
        getattr(highspy.HighsModelStatus, "kInterrupt", None),
        getattr(highspy.HighsModelStatus, "kHighsInterrupt", None),
    } - {None}
    if model_status in interrupt_statuses:
        return SolverTermination.INTERRUPTED

    if model_status == highspy.HighsModelStatus.kMemoryLimit:
        return SolverTermination.MEMORY_LIMIT
    return SolverTermination.OTHER


def solve_with_highs(request: WorkerRequest) -> WorkerResponse:
    """Execute continuous LP solve using highspy directly.

    Returns raw solver candidates and execution metadata in a WorkerResponse.
    Never raises exceptions for mathematical or solver outcomes.
    """
    highs_version = "unknown"
    try:
        import highspy
    except ImportError as exc:
        return WorkerResponse(
            contract_version=request.contract_version,
            request_id=request.request_id,
            model_sha256=request.model_sha256,
            ir_sha256=request.ir_sha256,
            status=WorkerStatus.FAILED,
            termination=SolverTermination.NOT_RUN,
            raw_solver_status="NOT_INSTALLED",
            solver_name="HiGHS",
            solver_version=highs_version,
            failure=WorkerFailure(
                code=WorkerFailureCode.UNAVAILABLE,
                message=f"highspy is not available: {exc}",
            ),
        )

    try:
        highs = highspy.Highs()  # type: ignore[no-untyped-call]
        highs_version = str(highs.version())

        problem = request.problem
        lp = highspy.HighsLp()
        lp.num_col_ = problem.num_variables
        lp.num_row_ = problem.num_rows

        lp.col_cost_ = np.array(problem.cost, dtype=np.float64)
        lp.col_lower_ = np.array(
            [v if v is not None else -highspy.kHighsInf for v in problem.col_lower],
            dtype=np.float64,
        )
        lp.col_upper_ = np.array(
            [v if v is not None else highspy.kHighsInf for v in problem.col_upper],
            dtype=np.float64,
        )
        lp.row_lower_ = np.array(
            [v if v is not None else -highspy.kHighsInf for v in problem.row_lower],
            dtype=np.float64,
        )
        lp.row_upper_ = np.array(
            [v if v is not None else highspy.kHighsInf for v in problem.row_upper],
            dtype=np.float64,
        )
        lp.offset_ = float(problem.offset)

        col_start, row_index, csc_values = csr_to_csc(
            problem.num_variables,
            problem.num_rows,
            problem.row_start,
            problem.col_index,
            problem.values,
        )
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = col_start
        lp.a_matrix_.index_ = row_index
        lp.a_matrix_.value_ = csc_values

        # Apply solver options
        options = request.options
        highs.setOptionValue("output_flag", False)
        highs.setOptionValue("time_limit", max(0.0001, float(options.time_limit_seconds)))
        highs.setOptionValue("threads", int(options.threads))
        highs.setOptionValue("presolve", "on" if options.presolve else "off")
        highs.setOptionValue(
            "primal_feasibility_tolerance", float(options.primal_feasibility_tolerance)
        )
        highs.setOptionValue(
            "dual_feasibility_tolerance", float(options.dual_feasibility_tolerance)
        )
        highs.setOptionValue("random_seed", int(options.random_seed))

        if options.algorithm == LPAlgorithm.SIMPLEX:
            highs.setOptionValue("solver", "simplex")
        elif options.algorithm == LPAlgorithm.IPM:
            highs.setOptionValue("solver", "ipm")
        else:
            highs.setOptionValue("solver", "choose")

        pass_status = highs.passModel(lp)
        if pass_status != highspy.HighsStatus.kOk:
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.NUMERICAL_ERROR,
                raw_solver_status=str(pass_status),
                solver_name="HiGHS",
                solver_version=highs_version,
                failure=WorkerFailure(
                    code=WorkerFailureCode.SOLVER_ERROR,
                    message=f"HiGHS passModel failed with status {pass_status}",
                ),
            )

        t0 = time.perf_counter()
        run_status = highs.run()
        t1 = time.perf_counter()
        runtime_seconds = max(0.0, t1 - t0)

        model_status = highs.getModelStatus()
        raw_solver_status = str(model_status)
        termination = _map_highs_model_status(model_status)

        info = highs.getInfo()
        sol = highs.getSolution()

        iterations: int | None = None
        if info.valid:
            iterations = int(info.simplex_iteration_count) + int(info.ipm_iteration_count)

        # Candidate extraction: finite values only, matching exact IR dimensions
        primal_sol: tuple[float, ...] | None = None
        if (
            termination
            not in (SolverTermination.INFEASIBLE, SolverTermination.UNBOUNDED_OR_INFEASIBLE)
            and sol.value_valid
            and len(sol.col_value) == problem.num_variables
        ):
            vals = [float(v) for v in sol.col_value]
            if all(math.isfinite(v) for v in vals):
                primal_sol = tuple(vals)

        dual_sol: tuple[float, ...] | None = None
        if (
            termination
            not in (SolverTermination.UNBOUNDED, SolverTermination.UNBOUNDED_OR_INFEASIBLE)
            and sol.dual_valid
            and len(sol.row_dual) == problem.num_rows
        ):
            vals = [float(v) for v in sol.row_dual]
            if all(math.isfinite(v) for v in vals):
                dual_sol = tuple(vals)

        obj_val: float | None = None
        if (
            termination
            not in (
                SolverTermination.INFEASIBLE,
                SolverTermination.UNBOUNDED,
                SolverTermination.UNBOUNDED_OR_INFEASIBLE,
            )
            and sol.value_valid
            and math.isfinite(info.objective_function_value)
        ):
            obj_val = float(info.objective_function_value)

        farkas_ray: tuple[float, ...] | None = None
        if termination in (
            SolverTermination.INFEASIBLE,
            SolverTermination.UNBOUNDED_OR_INFEASIBLE,
        ):
            _, has_dray, dray = highs.getDualRay()  # type: ignore[attr-defined]
            if has_dray and dray is not None and len(dray) == problem.num_rows:
                vals = [float(v) for v in dray]
                if all(math.isfinite(v) for v in vals):
                    farkas_ray = tuple(vals)

        primal_ray: tuple[float, ...] | None = None
        if termination in (
            SolverTermination.UNBOUNDED,
            SolverTermination.UNBOUNDED_OR_INFEASIBLE,
        ):
            _, has_pray, pray = highs.getPrimalRay()  # type: ignore[attr-defined]
            if has_pray and pray is not None and len(pray) == problem.num_variables:
                vals = [float(v) for v in pray]
                if all(math.isfinite(v) for v in vals):
                    primal_ray = tuple(vals)

        # Record diagnostics
        diagnostics: dict[str, str] = {
            "model_status": raw_solver_status,
            "run_status": str(run_status),
        }
        if info.valid:
            diagnostics["simplex_iterations"] = str(info.simplex_iteration_count)
            diagnostics["ipm_iterations"] = str(info.ipm_iteration_count)

        # Optional IIS extraction for diagnostics only (never proof)
        if termination == SolverTermination.INFEASIBLE:
            with contextlib.suppress(Exception):
                _, iis = highs.getIis()
                if iis.valid_:
                    diagnostics["iis_num_rows"] = str(len(iis.row_index_))
                    diagnostics["iis_num_cols"] = str(len(iis.col_index_))

        if termination == SolverTermination.TIME_LIMIT:
            status = WorkerStatus.TIMED_OUT
        else:
            status = WorkerStatus.FINISHED

        return WorkerResponse(
            contract_version=request.contract_version,
            request_id=request.request_id,
            model_sha256=request.model_sha256,
            ir_sha256=request.ir_sha256,
            status=status,
            termination=termination,
            raw_solver_status=raw_solver_status,
            solver_name="HiGHS",
            solver_version=highs_version,
            objective_value=obj_val,
            primal_solution=primal_sol,
            dual_solution=dual_sol,
            farkas_ray=farkas_ray,
            primal_ray=primal_ray,
            iterations=iterations,
            runtime_seconds=runtime_seconds,
            diagnostics=diagnostics,
        )

    except Exception as exc:
        return WorkerResponse(
            contract_version=request.contract_version,
            request_id=request.request_id,
            model_sha256=request.model_sha256,
            ir_sha256=request.ir_sha256,
            status=WorkerStatus.FAILED,
            termination=SolverTermination.NUMERICAL_ERROR,
            raw_solver_status="EXCEPTION",
            solver_name="HiGHS",
            solver_version=highs_version,
            failure=WorkerFailure(
                code=WorkerFailureCode.SOLVER_ERROR,
                message=f"Exception during HiGHS solve: {exc}"[:MAX_RAW_TEXT],
            ),
        )


class WindowsJobObject:
    """Win32 Job Object wrapper for worker process memory bounds and lifecycle management."""

    def __init__(self, memory_limit_bytes: int | None = None) -> None:
        self.handle: int | None = None
        if sys.platform != "win32":
            return

        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h_job = kernel32.CreateJobObjectW(None, None)
            if not h_job:
                return
            self.handle = h_job

            class IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class JobObjectBasicLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JobObjectExtendedLimitInformation(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JobObjectBasicLimitInformation),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryLimit", ctypes.c_size_t),
                    ("PeakJobMemoryLimit", ctypes.c_size_t),
                ]

            job_object_limit_kill_on_job_close = 0x2000
            job_object_limit_job_memory = 0x0200

            info = JobObjectExtendedLimitInformation()
            limit_flags = job_object_limit_kill_on_job_close
            if memory_limit_bytes is not None and memory_limit_bytes > 0:
                limit_flags |= job_object_limit_job_memory
                info.JobMemoryLimit = memory_limit_bytes

            info.BasicLimitInformation.LimitFlags = limit_flags
            job_object_extended_limit_information = 9
            kernel32.SetInformationJobObject(
                h_job,
                job_object_extended_limit_information,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
        except Exception:
            self.handle = None

    def assign_process(self, proc: subprocess.Popen[Any]) -> bool:
        """Assign child subprocess to this Windows Job Object."""
        if self.handle is None or sys.platform != "win32":
            return False
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            proc_handle = getattr(proc, "_handle", None)
            if proc_handle and isinstance(proc_handle, int):
                return bool(kernel32.AssignProcessToJobObject(self.handle, proc_handle))
            return False
        except Exception:
            return False

    def terminate(self, exit_code: int = 1) -> None:
        """Terminate all processes assigned to this Job Object."""
        if self.handle is None or sys.platform != "win32":
            return
        with contextlib.suppress(Exception):
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject(self.handle, exit_code)

    def close(self) -> None:
        """Close the Job Object handle."""
        if self.handle is not None and sys.platform == "win32":
            with contextlib.suppress(Exception):
                import ctypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle(self.handle)
            self.handle = None


class HighsAdapter(SolverAdapter):
    """Production HiGHS continuous-LP solver adapter satisfying SolverAdapter.

    Spawns an isolated Python worker subprocess bounded by a Windows Job Object
    to protect the host against unhandled C++ crashes, memory exhaustion, and runaway threads.
    """

    def __init__(
        self,
        memory_limit_bytes: int = 4 * 1024 * 1024 * 1024,
        timeout_buffer_seconds: float = 3.0,
        in_process: bool = False,
    ) -> None:
        self._memory_limit_bytes = memory_limit_bytes
        self._timeout_buffer_seconds = max(0.5, timeout_buffer_seconds)
        self._in_process = in_process

    @property
    def name(self) -> str:
        return "HiGHS"

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        """Solve request in an isolated worker process. Never raises for solver outcomes."""
        if self._in_process:
            return solve_with_highs(request)

        # Prepare isolated worker command
        cmd = [sys.executable, "-m", "qorvexa.solver_fabric.highs_worker"]

        # Use a minimal child environment to avoid leaking host secrets
        src_dir = str(Path(__file__).resolve().parent.parent.parent)
        env = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
            "PATH": os.environ.get("PATH", ""),
            "TEMP": os.environ.get("TEMP", r"C:\Temp"),
            "TMP": os.environ.get("TMP", r"C:\Temp"),
            "PYTHONPATH": src_dir,
            "PYTHONUNBUFFERED": "1",
        }
        if "PATHEXT" in os.environ:
            env["PATHEXT"] = os.environ["PATHEXT"]

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        job_obj = WindowsJobObject(memory_limit_bytes=self._memory_limit_bytes)

        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            job_obj.close()
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.NOT_RUN,
                raw_solver_status="SPAWN_FAILED",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=WorkerFailureCode.UNAVAILABLE,
                    message=f"Failed to spawn solver worker process: {exc}"[:MAX_RAW_TEXT],
                ),
            )

        job_obj.assign_process(proc)

        wall_timeout = request.options.time_limit_seconds + self._timeout_buffer_seconds
        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=request.to_json_bytes(), timeout=wall_timeout
            )
        except subprocess.TimeoutExpired:
            # Enforce hard kill on timeout
            with contextlib.suppress(OSError):
                proc.kill()
            job_obj.terminate(exit_code=1)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=1.0)
            job_obj.close()
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.TIMED_OUT,
                termination=SolverTermination.SUPERVISOR_KILLED,
                raw_solver_status="TIMEOUT_SUPERVISOR_KILLED",
                solver_name="HiGHS",
                solver_version="unknown",
                runtime_seconds=wall_timeout,
            )
        finally:
            job_obj.close()

        if proc.returncode != 0:
            stderr_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
            # Check for Windows NT STATUS_QUOTA_EXCEEDED (0xC0000044) or Linux OOM
            is_oom = (
                proc.returncode in (-1073741756, 3221225540, 137)
                or "Memory limit" in stderr_msg
                or "std::bad_alloc" in stderr_msg
            )
            code = WorkerFailureCode.MEMORY_LIMIT if is_oom else WorkerFailureCode.CRASHED
            term = SolverTermination.MEMORY_LIMIT if is_oom else SolverTermination.CRASHED
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=term,
                raw_solver_status=f"EXIT_{proc.returncode}",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=code,
                    message=(f"Worker process exited with code {proc.returncode}: {stderr_msg}")[
                        :MAX_RAW_TEXT
                    ],
                ),
            )

        if not stdout_bytes:
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.OTHER,
                raw_solver_status="EMPTY_RESPONSE",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=WorkerFailureCode.SOLVER_ERROR,
                    message="Worker process emitted empty output",
                ),
            )

        if len(stdout_bytes) > MAX_RESPONSE_BYTES:
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.OTHER,
                raw_solver_status="OVERSIZED_RESPONSE",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=WorkerFailureCode.SOLVER_ERROR,
                    message="Worker output exceeded response size limit",
                ),
            )

        # Slice stdout bytes to isolate the JSON object {...} in case of uncaptured C output
        start_idx = stdout_bytes.find(b"{")
        end_idx = stdout_bytes.rfind(b"}")
        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            json_bytes = stdout_bytes[start_idx : end_idx + 1]
        else:
            json_bytes = stdout_bytes

        try:
            response = WorkerResponse.from_json_bytes(json_bytes)
        except Exception as exc:
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.OTHER,
                raw_solver_status="MALFORMED_RESPONSE",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=WorkerFailureCode.SOLVER_ERROR,
                    message=f"Failed to parse worker response: {exc}"[:MAX_RAW_TEXT],
                ),
            )

        # Strictly verify identity and SHA-256 bindings
        if (
            response.request_id != request.request_id
            or response.model_sha256 != request.model_sha256
            or response.ir_sha256 != request.ir_sha256
        ):
            return WorkerResponse(
                contract_version=request.contract_version,
                request_id=request.request_id,
                model_sha256=request.model_sha256,
                ir_sha256=request.ir_sha256,
                status=WorkerStatus.FAILED,
                termination=SolverTermination.OTHER,
                raw_solver_status="BINDING_MISMATCH",
                solver_name="HiGHS",
                solver_version="unknown",
                failure=WorkerFailure(
                    code=WorkerFailureCode.INVALID_REQUEST,
                    message="Worker response request_id or SHA-256 hashes do not match request",
                ),
            )

        return response
