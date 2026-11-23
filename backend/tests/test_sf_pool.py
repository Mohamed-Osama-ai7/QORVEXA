# ruff: noqa: E501
"""SolverPool: bounded concurrent multi-process execution, fault containment, lifecycle, and
preservation of independent mathematical verification.

Real worker processes are used throughout (no mocks). Fault scenarios run the protocol-faithful
fault worker ``sf_pool_fault_worker.py``. Expected objective values come from an independent exact
oracle (fractional knapsack with ``Fraction``), never from the solver under test.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from qorvexa.contracts.solver_runtime import MathOutcome, SolverTermination
from qorvexa.industrial.production_planning import (
    Consumption,
    ObjectiveMode,
    PlanningMetadata,
    PlanningProblem,
    Product,
    Resource,
    ResourceKind,
    compile_problem,
)
from qorvexa.solver_fabric import LPOrchestrator
from qorvexa.solver_fabric.canonical_lp import LoweredLP
from qorvexa.solver_fabric.highs_adapter import HighsAdapter
from qorvexa.solver_fabric.pool import (
    JOB_TRANSITIONS,
    WORKER_TRANSITIONS,
    IllegalTransitionError,
    PoolConfig,
    PoolJobState,
    SolverPool,
    WorkerState,
    _Job,
    _Worker,
    pid_alive,
)
from qorvexa.solver_fabric.worker_contract import (
    LoweredLPPayload,
    SolverOptions,
    WorkerFailureCode,
    WorkerRequest,
    WorkerStatus,
)

FAULT = Path(__file__).with_name("sf_pool_fault_worker.py")
WIN = sys.platform == "win32"


# ============================================================================ helpers
def fault_pool(mode: str, **kw: Any) -> SolverPool:
    return SolverPool(PoolConfig(worker_argv=(sys.executable, str(FAULT), mode), **kw))


def close(pool: SolverPool, *, drain: bool = False) -> None:
    rep = pool.shutdown(drain=drain, timeout=20)
    assert rep.unterminated_pids == (), f"orphan worker processes: {rep.unterminated_pids}"
    assert all(not pid_alive(p) for p in pool.known_pids())


def request(
    rid: str = "req-1", *, time_limit: float = 30.0, kind: str = "bounded"
) -> WorkerRequest:
    """Tiny LPs with analytically known answers (same shapes as the adapter tests)."""
    if kind == "bounded":  # min x1 + 2x2, 1 <= x1+x2 <= 4, x in [0,3]^2 -> x=(1,0), obj 1
        ir = LoweredLP(
            2,
            1,
            (1.0, 2.0),
            (0.0, 0.0),
            (3.0, 3.0),
            (1.0,),
            (4.0,),
            (0, 2),
            (0, 1),
            (1.0, 1.0),
            0.0,
        )
    elif kind == "infeasible":  # x1 + x2 >= 5 with x in [0,1]^2
        ir = LoweredLP(
            2,
            1,
            (1.0, 1.0),
            (0.0, 0.0),
            (1.0, 1.0),
            (5.0,),
            (math.inf,),
            (0, 2),
            (0, 1),
            (1.0, 1.0),
            0.0,
        )
    else:  # unbounded: min -x1 s.t. x1 - x2 <= 1, x >= 0
        ir = LoweredLP(
            2,
            1,
            (-1.0, 0.0),
            (0.0, 0.0),
            (math.inf, math.inf),
            (-math.inf,),
            (1.0,),
            (0, 2),
            (0, 1),
            (1.0, -1.0),
            0.0,
        )
    return WorkerRequest(
        request_id=rid, model_sha256="0" * 64, ir_sha256=ir.ir_sha256, compiler_version="1.0.0",
        problem=LoweredLPPayload.from_lowered(ir), options=SolverOptions(time_limit_seconds=time_limit),
    )  # fmt: skip


def knapsack(seed: int) -> tuple[PlanningProblem, Fraction]:
    """One resource, three products: max sum v_i x_i, sum a_i x_i <= C, 0 <= x_i <= u_i.
    The optimum is the exact greedy fractional knapsack (independent of any solver)."""
    rnd = [(seed * 7919 + k * 104729) % 97 for k in range(12)]
    vals = [Fraction(3 + rnd[i] % 17) for i in range(3)]
    use = [Fraction(1 + rnd[3 + i] % 9, 10) for i in range(3)]
    caps = [Fraction(10 + rnd[6 + i] % 40) for i in range(3)]
    cap = Fraction(5 + rnd[9] % 20)
    best, left = Fraction(0), cap
    for i in sorted(range(3), key=lambda i: -(vals[i] / use[i])):
        x = min(caps[i], left / use[i])
        best += vals[i] * x
        left -= use[i] * x
    dec = lambda f: Decimal(f.numerator) / Decimal(f.denominator)  # noqa: E731
    p = PlanningProblem(
        metadata=PlanningMetadata(problem_id=f"KS-{seed}", title="synthetic knapsack"),
        mode=ObjectiveMode.MAXIMIZE_MARGIN, currency="EGP",
        products=tuple(Product(id=f"P{i}", name=f"P{i}", unit="piece", value_per_unit=dec(vals[i]),
                               value_currency="EGP", max_quantity=dec(caps[i])) for i in range(3)),
        resources=(Resource(id="R", name="hours", kind=ResourceKind.MACHINE, unit="h", capacity=dec(cap)),),
        consumptions=tuple(Consumption(resource_id="R", product_id=f"P{i}", amount_per_unit=dec(use[i]),
                                       resource_unit="h", product_unit="piece") for i in range(3)),
    )  # fmt: skip
    return p, best


def wait_until(pred: Any, timeout: float = 20.0) -> bool:
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ============================================================================ lifecycle tables
class TestStateMachines:
    def test_job_transition_table(self) -> None:
        j = _Job("e", 1, request(), b"")
        with pytest.raises(IllegalTransitionError):
            j.move(PoolJobState.RUNNING)  # QUEUED -> RUNNING skips admission
        j.move(PoolJobState.ADMITTED)
        j.move(PoolJobState.RUNNING)
        j.move(PoolJobState.TIMED_OUT)
        for s in PoolJobState:
            with pytest.raises(IllegalTransitionError):
                j.move(s)  # terminal states are final
        assert JOB_TRANSITIONS[PoolJobState.REJECTED] == frozenset()
        assert PoolJobState.TIMED_OUT not in JOB_TRANSITIONS[PoolJobState.QUEUED]

    def test_worker_transition_table(self) -> None:
        w = _Worker("w", 0)
        with pytest.raises(IllegalTransitionError):
            w.move(WorkerState.BUSY)  # CREATED -> BUSY without READY
        for s in (
            WorkerState.READY,
            WorkerState.BUSY,
            WorkerState.COMPLETING,
            WorkerState.READY,
            WorkerState.TERMINATING,
            WorkerState.TERMINATED,
        ):
            w.move(s)
        assert w.history == [
            "CREATED",
            "READY",
            "BUSY",
            "COMPLETING",
            "READY",
            "TERMINATING",
            "TERMINATED",
        ]
        with pytest.raises(IllegalTransitionError):
            w.move(WorkerState.READY)
        assert WORKER_TRANSITIONS[WorkerState.TERMINATED] == frozenset()

    def test_config_validation(self) -> None:
        for bad in (
            {"max_workers": 0},
            {"max_workers": 65},
            {"max_queue": -1},
            {"max_attempts": 4},
            {"timeout_buffer_s": 0},
            {"max_time_limit_s": 0},
        ):
            with pytest.raises(ValueError, match="INVALID_POOL_CONFIG"):
                PoolConfig(**bad)


# ============================================================================ concurrency
class TestConcurrency:
    def test_concurrent_lp_jobs_are_independently_verified(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=3, max_queue=16))
        try:
            orch = LPOrchestrator(pool)
            cases = [knapsack(s) for s in range(9)]
            out: dict[int, Any] = {}

            def run(i: int) -> None:
                out[i] = orch.solve_json(compile_problem(cases[i][0]).spec_json, job_id=f"ks-{i}")

            threads = [threading.Thread(target=run, args=(i,)) for i in range(9)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(120)
            used = set()
            for i, (_, expected) in enumerate(cases):
                res = out[i].result
                assert res is not None and res.outcome is MathOutcome.SOLVED, (i, res)
                assert res.objective_value == pytest.approx(float(expected), rel=1e-9, abs=1e-9)
                used.add(res.metadata.get("solver_adapter"))
            assert used == {"HiGHS-pool"}
            assert pool.stats().counters["completed"] == 9
        finally:
            close(pool)

    def test_jobs_really_run_in_parallel_up_to_the_limit(self) -> None:
        pool = fault_pool("slow:1.0", max_workers=3, max_queue=8)
        try:
            assert pool.wait_ready(60)
            t0 = time.perf_counter()
            hs = [pool.submit(request(f"p{i}")) for i in range(3)]
            for h in hs:
                assert h.result(30).status is WorkerStatus.FINISHED
            wall = time.perf_counter() - t0
            infos = [h.info() for h in hs]
            assert wall < 2.5, wall  # three 1 s jobs, sequentially >= 3 s
            assert len({i.worker_id for i in infos}) == 3
        finally:
            close(pool)

    def test_worker_limit_is_never_exceeded(self) -> None:
        pool = fault_pool("slow:0.4", max_workers=2, max_queue=8)
        try:
            assert pool.wait_ready(60)
            peak = 0
            hs = [pool.submit(request(f"l{i}")) for i in range(6)]
            while not all(h.done() for h in hs):
                peak = max(peak, sum(1 for h in hs if h.state is PoolJobState.RUNNING))
                time.sleep(0.01)
            assert peak <= 2 and pool.stats().counters["completed"] == 6
            assert pool.stats().counters.get("spawned") == 2  # no extra processes
        finally:
            close(pool)

    def test_saturation_rejects_explicitly_and_keeps_fifo(self) -> None:
        pool = fault_pool("slow:0.3", max_workers=1, max_queue=2)
        try:
            assert pool.wait_ready(60)
            hs = [pool.submit(request(f"s{i}")) for i in range(5)]
            states = [h.state for h in hs]
            assert states[3:] == [PoolJobState.REJECTED, PoolJobState.REJECTED]
            for h in hs[3:]:
                r = h.result(0)
                assert (
                    r.status is WorkerStatus.FAILED and r.termination is SolverTermination.NOT_RUN
                )
                assert r.failure is not None and r.failure.code is WorkerFailureCode.UNAVAILABLE
                assert "POOL_SATURATED" in r.raw_solver_status and r.primal_solution is None
            for h in hs[:3]:
                h.result(30)
            finished = [h._job.finished for h in hs[:3]]
            assert finished == sorted(finished)  # FIFO completion order
        finally:
            close(pool)

    def test_round_robin_assignment_is_deterministic(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=3))
        try:
            assert pool.wait_ready(60)
            ids = []
            for i in range(6):
                h = pool.submit(request(f"rr{i}"))
                h.result(60)
                ids.append(h.info().worker_id)
            assert ids[:3] == ids[3:] and len(set(ids[:3])) == 3
        finally:
            close(pool)

    def test_concurrent_results_are_bound_to_their_own_requests(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=3, max_queue=16))
        try:
            reqs = [
                request(f"iso-{i}", kind=("bounded", "infeasible", "unbounded")[i % 3])
                for i in range(9)
            ]
            reqs.append(request("iso-0"))  # duplicate request id submitted concurrently
            hs = [pool.submit(r) for r in reqs]
            for r, h in zip(reqs, hs, strict=True):
                resp = h.result(120)
                assert (resp.request_id, resp.ir_sha256) == (r.request_id, r.ir_sha256)
                assert resp.diagnostics["pool_execution_id"] == h.execution_id
            assert len({h.execution_id for h in hs}) == len(hs)
        finally:
            close(pool)


# ============================================================================ faults
class TestFaults:
    def test_crash_is_contained_and_worker_replaced(self) -> None:
        pool = fault_pool("crash_after:1", max_workers=1, max_queue=4)
        try:
            first, second = pool.submit(request("c1")), None
            assert first.result(60).status is WorkerStatus.FINISHED
            second = pool.submit(request("c2"))
            r = second.result(60)
            assert second.state is PoolJobState.FAILED and r.status is WorkerStatus.FAILED
            assert r.failure is not None and r.failure.code is WorkerFailureCode.CRASHED
            assert r.termination is SolverTermination.CRASHED and r.primal_solution is None
            third = pool.submit(request("c3"))  # runs on the replacement process
            assert third.result(60).status is WorkerStatus.FINISHED
            assert pool.stats().counters["replaced"] >= 1
            assert first.info().worker_id != third.info().worker_id
        finally:
            close(pool)

    def test_timeout_kills_the_worker_and_never_returns_a_solution(self) -> None:
        pool = fault_pool("hang_on_solve", max_workers=1, timeout_buffer_s=0.3)
        try:
            assert pool.wait_ready(60)
            pids = {w.pid for w in pool.worker_snapshots()} | {
                w.child_pid for w in pool.worker_snapshots()
            }
            t0 = time.perf_counter()
            h = pool.submit(request("t1", time_limit=0.2))
            r = h.result(30)
            assert time.perf_counter() - t0 < 5
            assert h.state is PoolJobState.TIMED_OUT and r.status is WorkerStatus.TIMED_OUT
            assert r.termination is SolverTermination.SUPERVISOR_KILLED
            assert r.primal_solution is None and r.objective_value is None
            assert wait_until(lambda: all(not pid_alive(p) for p in pids if p))
            assert pool.stats().counters["killed_timeout"] == 1
        finally:
            close(pool)

    def test_cancellation_queued_running_and_finished(self) -> None:
        pool = fault_pool("hang_on_solve", max_workers=1, max_queue=4)
        try:
            assert pool.wait_ready(60)
            running = pool.submit(request("x1", time_limit=600))
            queued = pool.submit(request("x2", time_limit=600))
            assert wait_until(lambda: running.state is PoolJobState.RUNNING)
            assert queued.cancel() and queued.state is PoolJobState.CANCELLED
            assert queued.info().attempts == ()  # never ran
            t0 = time.perf_counter()
            assert running.cancel() and running.state is PoolJobState.CANCELLED
            r = running.result(5)
            assert time.perf_counter() - t0 < 5
            assert r.status is WorkerStatus.FAILED and r.raw_solver_status == "CANCELLED"
            assert r.primal_solution is None
            assert not running.cancel() or running.state is PoolJobState.CANCELLED
        finally:
            close(pool)

    @pytest.mark.parametrize(
        ("mode", "code"),
        [
            ("garbage", WorkerFailureCode.SOLVER_ERROR),
            ("oversize", WorkerFailureCode.SOLVER_ERROR),
            ("bad_json", WorkerFailureCode.SOLVER_ERROR),
            ("wrong_seq", WorkerFailureCode.SOLVER_ERROR),
            ("wrong_binding", WorkerFailureCode.INVALID_REQUEST),
            ("fatal", WorkerFailureCode.SOLVER_ERROR),
        ],
    )
    def test_malformed_ipc_is_rejected(self, mode: str, code: WorkerFailureCode) -> None:
        pool = fault_pool(mode, max_workers=1)
        try:
            h = pool.submit(request(f"m-{mode}"))
            r = h.result(60)
            assert h.state is PoolJobState.FAILED, (mode, h.info())
            assert r.status is WorkerStatus.FAILED and r.primal_solution is None
            assert r.failure is not None and r.failure.code is code, (mode, r.failure)
            assert h.info().attempts[-1].outcome == "WORKER_FAILED"
        finally:
            close(pool)

    def test_unexpected_termination_of_an_idle_worker_is_detected(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=1))
        try:
            assert pool.wait_ready(60)
            w = pool.worker_snapshots()[0]
            for p in (w.child_pid, w.pid):
                if p:
                    os.kill(p, 9 if not WIN else 15)
            assert wait_until(lambda: pool.stats().counters.get("crashed_idle", 0) >= 1)
            assert pool.submit(request("after-kill")).result(60).status is WorkerStatus.FINISHED
            assert pool.worker_snapshots()[0].worker_id != w.worker_id
        finally:
            close(pool)

    @pytest.mark.parametrize("mode", ["exit_at_start", "bad_hello", "no_hello"])
    def test_unstartable_workers_never_leave_stuck_jobs(self, mode: str) -> None:
        pool = fault_pool(mode, max_workers=1, max_spawn_failures=2, startup_timeout_s=1.0)
        try:
            h = pool.submit(request(f"u-{mode}"))
            r = h.result(30)
            assert h.state in (PoolJobState.FAILED, PoolJobState.REJECTED)
            assert r.failure is not None and r.failure.code is WorkerFailureCode.UNAVAILABLE
            late = pool.submit(request("late"))
            assert (
                late.state is PoolJobState.REJECTED
                and "POOL_UNAVAILABLE" in late.result(0).raw_solver_status
            )
        finally:
            close(pool)

    def test_retries_are_explicit_bounded_and_recorded(self, tmp_path: Path) -> None:
        marker = tmp_path / "crashed-once"
        pool = fault_pool(f"crash_once:{marker}", max_workers=1, max_attempts=2)
        try:
            h = pool.submit(request("retry-1"))
            assert (
                h.result(60).status is WorkerStatus.FINISHED and h.state is PoolJobState.COMPLETED
            )
            assert [a.outcome for a in h.info().attempts] == ["WORKER_FAILED", "COMPLETED"]
            assert pool.stats().counters["retried"] == 1
        finally:
            close(pool)
        marker.unlink()
        no_retry = fault_pool(f"crash_once:{marker}", max_workers=1)  # default max_attempts=1
        try:
            h = no_retry.submit(request("retry-2"))
            assert h.result(60).status is WorkerStatus.FAILED and len(h.info().attempts) == 1
        finally:
            close(no_retry)

    def test_timeouts_are_never_retried(self) -> None:
        pool = fault_pool("hang_on_solve", max_workers=1, max_attempts=3, timeout_buffer_s=0.2)
        try:
            h = pool.submit(request("nr", time_limit=0.2))
            assert h.result(60).status is WorkerStatus.TIMED_OUT and len(h.info().attempts) == 1
        finally:
            close(pool)

    @pytest.mark.skipif(not WIN, reason="memory enforcement verified via Windows Job Objects only")
    def test_memory_limit_is_enforced_not_advisory(self) -> None:
        # The idle worker interpreter commits ~430 MB (numpy/BLAS/highspy) on this machine.
        tiny = SolverPool(
            PoolConfig(max_workers=1, memory_limit_bytes=200 * 1024 * 1024, max_spawn_failures=1)
        )
        try:  # a limit below the worker's own footprint stops it from starting at all
            r0 = tiny.submit(request("mem0")).result(60)
            assert r0.failure is not None and r0.failure.code is WorkerFailureCode.UNAVAILABLE
        finally:
            close(tiny)
        limited = fault_pool("memhog:900", max_workers=1, memory_limit_bytes=800 * 1024 * 1024)
        try:
            h = limited.submit(request("mem"))
            r = h.result(60)
            assert h.state is PoolJobState.FAILED and r.failure is not None
            assert r.failure.code is WorkerFailureCode.MEMORY_LIMIT, r.failure
        finally:
            close(limited)
        unlimited = fault_pool("memhog:900", max_workers=1, memory_limit_bytes=0)
        try:  # control: the same allocation succeeds without the limit
            assert unlimited.submit(request("mem2")).result(60).status is WorkerStatus.FINISHED
        finally:
            close(unlimited)


# ============================================================================ lifecycle
class TestLifecycle:
    def test_graceful_shutdown_drains_and_rejects_new_work(self) -> None:
        pool = fault_pool("slow:0.3", max_workers=2, max_queue=8)
        hs = [pool.submit(request(f"g{i}")) for i in range(4)]
        rep = pool.shutdown(drain=True, timeout=30)
        assert rep.drained and rep.cancelled_jobs == 0 and rep.unterminated_pids == ()
        assert all(h.state is PoolJobState.COMPLETED for h in hs)
        late = pool.submit(request("late"))
        assert (
            late.state is PoolJobState.REJECTED
            and "POOL_SHUTDOWN" in late.result(0).raw_solver_status
        )
        for w in pool.worker_snapshots(include_retired=True):
            assert w.history[-1] == "TERMINATED"
            assert all(
                b in WORKER_TRANSITIONS[WorkerState(a)]
                for a, b in zip(w.history, w.history[1:], strict=False)
            )

    def test_forced_shutdown_kills_running_and_cancels_queued(self) -> None:
        pool = fault_pool("hang_on_solve", max_workers=2, max_queue=8)
        hs = [pool.submit(request(f"f{i}", time_limit=600)) for i in range(5)]
        assert wait_until(lambda: sum(h.state is PoolJobState.RUNNING for h in hs) == 2)
        t0 = time.perf_counter()
        rep = pool.shutdown(drain=False)
        assert time.perf_counter() - t0 < 15
        assert rep.unterminated_pids == () and rep.cancelled_jobs == 3 and rep.killed_workers >= 2
        assert all(h.done() and h.state is PoolJobState.CANCELLED for h in hs)

    def test_drain_timeout_falls_back_to_force(self) -> None:
        pool = fault_pool("hang_on_solve", max_workers=1)
        h = pool.submit(request("d", time_limit=600))
        assert wait_until(lambda: h.state is PoolJobState.RUNNING)
        rep = pool.shutdown(drain=True, timeout=0.5)
        assert not rep.drained and h.state is PoolJobState.CANCELLED and rep.unterminated_pids == ()

    def test_clean_restart_and_no_temp_file_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tmp = tmp_path / "tmp"
        tmp.mkdir()
        monkeypatch.setenv("TEMP", str(tmp))
        monkeypatch.setenv("TMP", str(tmp))
        for round_ in range(2):  # create, use, close, then a brand-new pool
            pool = SolverPool(PoolConfig(max_workers=2))
            try:
                assert pool.submit(request(f"r{round_}")).result(60).status is WorkerStatus.FINISHED
            finally:
                close(pool, drain=True)
        # The pool creates no files (pipes only). Antivirus software on this machine drops an
        # "_avast_" marker into TEMP when processes start; it is not ours and is ignored by name.
        assert [p for p in tmp.iterdir() if not p.name.startswith("_avast_")] == []

    def test_worker_recycling_after_max_jobs(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=1, max_jobs_per_worker=2))
        try:
            ids = []
            for i in range(5):
                h = pool.submit(request(f"rc{i}"))
                assert h.result(60).status is WorkerStatus.FINISHED
                ids.append(h.info().worker_id)
            assert ids[0] == ids[1] != ids[2] == ids[3] != ids[4]
            assert pool.stats().counters["recycled"] >= 2
        finally:
            close(pool)

    def test_time_limit_above_pool_cap_is_rejected(self) -> None:
        pool = SolverPool(PoolConfig(max_workers=1, max_time_limit_s=10, prestart=False))
        try:
            h = pool.submit(request("cap", time_limit=11))
            assert (
                h.state is PoolJobState.REJECTED
                and "TIME_LIMIT_EXCEEDS_POOL_CAP" in h.result(0).raw_solver_status
            )
        finally:
            close(pool)


# ============================================================================ mathematical safety
class TestMathematicalSafety:
    def test_solver_statuses_match_the_in_process_solver(self) -> None:
        direct = HighsAdapter(in_process=True)
        pool = SolverPool(PoolConfig(max_workers=2))
        try:
            for kind, term in (
                ("bounded", SolverTermination.OPTIMAL),
                ("infeasible", SolverTermination.INFEASIBLE),
                ("unbounded", None),
            ):
                req = request(f"st-{kind}", kind=kind)
                a, b = direct.solve(req), pool.solve(req)
                assert (
                    b.status,
                    b.termination,
                    b.objective_value,
                    b.primal_solution,
                    b.dual_solution,
                ) == (
                    a.status,
                    a.termination,
                    a.objective_value,
                    a.primal_solution,
                    a.dual_solution,
                )
                if term is not None:
                    assert b.termination is term
        finally:
            close(pool)

    def test_a_failing_job_does_not_corrupt_concurrent_valid_jobs(self) -> None:
        pool = fault_pool("crash_on_id:boom", max_workers=2, max_queue=16)
        try:
            orch = LPOrchestrator(pool)
            cases = {i: knapsack(100 + i) for i in range(5)}
            out: dict[str, Any] = {}

            def run(key: str, spec: str) -> None:
                out[key] = orch.solve_json(spec, job_id=key)

            jobs = [(f"ok-{i}", compile_problem(p).spec_json) for i, (p, _) in cases.items()]
            jobs.insert(2, ("boom-1", compile_problem(cases[0][0]).spec_json))
            threads = [threading.Thread(target=run, args=j) for j in jobs]
            for t in threads:
                t.start()
            for t in threads:
                t.join(120)
            assert out["boom-1"].result.outcome is MathOutcome.FAILED
            for i, (_, expected) in cases.items():
                res = out[f"ok-{i}"].result
                assert res.outcome is MathOutcome.SOLVED
                assert res.objective_value == pytest.approx(float(expected), rel=1e-9, abs=1e-9)
        finally:
            close(pool)

    def test_a_lying_worker_does_not_produce_a_verified_result(self) -> None:
        pool = fault_pool("lie", max_workers=1)
        try:
            p, _ = knapsack(7)
            out = LPOrchestrator(pool).solve_json(compile_problem(p).spec_json, job_id="liar")
            assert out.result is not None and out.result.outcome is not MathOutcome.SOLVED
            assert out.result.objective_value != -12345.0
        finally:
            close(pool)

    def test_infeasibility_through_the_pool_is_still_certified(self) -> None:
        p, _ = knapsack(3)
        forced = p.model_copy(
            update={
                "products": (
                    p.products[0].model_copy(
                        update={"min_quantity": Decimal(10_000), "max_quantity": None}
                    ),
                    *p.products[1:],
                )
            }
        )
        pool = SolverPool(PoolConfig(max_workers=1))
        try:
            res = (
                LPOrchestrator(pool)
                .solve_json(compile_problem(forced).spec_json, job_id="inf")
                .result
            )
            assert res is not None and res.outcome is MathOutcome.INFEASIBLE
        finally:
            close(pool)
