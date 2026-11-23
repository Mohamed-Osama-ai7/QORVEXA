"""Bounded multi-process solver pool (``SolverPool``), a drop-in ``SolverAdapter``.

``LPOrchestrator(SolverPool(...))`` works unchanged: the pool only moves ``WorkerRequest`` /
``WorkerResponse`` objects. It never interprets a result; the independent verifier stays the sole
authority on mathematical outcomes. A worker's answer is only "accepted at IPC level".

Architecture
------------
* ``max_workers`` persistent OS processes (``pool_worker``), one runner thread per worker plus one
  watchdog thread: the total thread count is ``max_workers + 1`` and never grows.
* One FIFO queue bounded by ``max_queue``. ``submit`` never blocks: when workers and queue are full
  the job is REJECTED (``POOL_SATURATED``) and a FAILED response is available immediately.
* Dispatch: strict FIFO; the head job goes to the next idle READY worker in round-robin order, so
  every worker gets work (no worker starvation) and a job's wait is bounded by the jobs ahead of it.
* Per-job wall clock = request time limit + ``timeout_buffer_s``, enforced by the watchdog, which
  kills the worker process tree. Timeouts and cancellations replace the worker; nothing is reused
  after a kill. A crash, timeout, cancellation or malformed answer is NEVER turned into a result.
* Retries are off by default (``max_attempts=1``); when enabled they apply only to worker crashes
  and IPC faults, are bounded (<= 3) and are recorded per job.

Lifecycles (explicit transition tables, illegal moves raise ``IllegalTransitionError``)::

    worker: CREATED -> READY <-> BUSY -> COMPLETING -> READY | FAILED -> TERMINATING -> TERMINATED
    job:    QUEUED -> ADMITTED -> RUNNING -> COMPLETED | FAILED | TIMED_OUT | CANCELLED
            (REJECTED = never queued; ADMITTED -> QUEUED when the worker died before the request
            was sent; RUNNING -> QUEUED only for an explicit retry)

``PoolJobState.COMPLETED`` means "the worker returned a contract-valid, correctly bound response".
It says nothing about mathematical validity or solver success.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import itertools
import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from collections import Counter, deque
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from qorvexa.contracts.solver_runtime import SolverTermination
from qorvexa.solver_fabric.highs_adapter import WindowsJobObject
from qorvexa.solver_fabric.pool_ipc import (
    IPC_VERSION,
    MAX_FRAME_BYTES,
    FrameCorruptError,
    FrameKind,
    IpcError,
    WorkerHello,
    read_frame,
    write_frame,
)
from qorvexa.solver_fabric.worker_contract import (
    CONTRACT_VERSION,
    MAX_RAW_TEXT,
    SolverAdapter,
    WorkerFailure,
    WorkerFailureCode,
    WorkerRequest,
    WorkerResponse,
    WorkerStatus,
)

POOL_VERSION: Final = "solver-pool/1.0"
_HISTORY: Final = 32
_SAMPLES: Final = 1024
_DEATH: Final = ("WORKER_EXITED", "IPC_FAILURE")  # problems that mean the process went away


class IllegalTransitionError(RuntimeError):
    """A lifecycle move that the transition table forbids."""


class WorkerState(StrEnum):
    CREATED = "CREATED"
    READY = "READY"
    BUSY = "BUSY"
    COMPLETING = "COMPLETING"
    FAILED = "FAILED"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"


class PoolJobState(StrEnum):
    QUEUED = "QUEUED"
    ADMITTED = "ADMITTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


W, J = WorkerState, PoolJobState
WORKER_TRANSITIONS: Final[dict[WorkerState, frozenset[WorkerState]]] = {
    W.CREATED: frozenset({W.READY, W.FAILED, W.TERMINATING}),
    W.READY: frozenset({W.BUSY, W.FAILED, W.TERMINATING}),
    W.BUSY: frozenset({W.COMPLETING, W.FAILED, W.TERMINATING}),
    W.COMPLETING: frozenset({W.READY, W.FAILED, W.TERMINATING}),
    W.FAILED: frozenset({W.TERMINATING, W.TERMINATED}),
    W.TERMINATING: frozenset({W.TERMINATED}),
    W.TERMINATED: frozenset(),
}
JOB_TRANSITIONS: Final[dict[PoolJobState, frozenset[PoolJobState]]] = {
    J.QUEUED: frozenset({J.ADMITTED, J.CANCELLED, J.FAILED}),
    J.ADMITTED: frozenset({J.RUNNING, J.QUEUED, J.CANCELLED, J.FAILED}),
    J.RUNNING: frozenset({J.COMPLETED, J.FAILED, J.TIMED_OUT, J.CANCELLED, J.QUEUED}),
    J.COMPLETED: frozenset(),
    J.FAILED: frozenset(),
    J.TIMED_OUT: frozenset(),
    J.CANCELLED: frozenset(),
    J.REJECTED: frozenset(),
}
TERMINAL_JOB_STATES: Final = frozenset(
    {J.COMPLETED, J.FAILED, J.TIMED_OUT, J.CANCELLED, J.REJECTED}
)


@dataclass(frozen=True)
class PoolConfig:
    max_workers: int = 2
    max_queue: int = 16  # jobs waiting beyond those held by workers
    memory_limit_bytes: int = 4 * 1024**3  # 0 = none. Windows: Job Object; POSIX: RLIMIT_AS
    timeout_buffer_s: float = 3.0
    startup_timeout_s: float = 60.0
    max_jobs_per_worker: int = 200  # recycle a worker after this many jobs
    max_time_limit_s: float = 3600.0  # larger request time limits are rejected
    max_attempts: int = 1  # 1 = no retry
    max_spawn_failures: int = 3  # consecutive failures before a worker slot is declared dead
    shutdown_grace_s: float = 2.0
    prestart: bool = True
    # Trusted deployment configuration only; never derived from a job payload.
    worker_argv: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        checks = (
            (1 <= self.max_workers <= 64, "max_workers must be 1..64"),
            (0 <= self.max_queue <= 10_000, "max_queue must be 0..10000"),
            (self.memory_limit_bytes >= 0, "memory_limit_bytes must be >= 0"),
            (0.1 <= self.timeout_buffer_s <= 600, "timeout_buffer_s must be 0.1..600"),
            (1 <= self.startup_timeout_s <= 600, "startup_timeout_s must be 1..600"),
            (1 <= self.max_jobs_per_worker <= 100_000, "max_jobs_per_worker must be 1..100000"),
            (0 < self.max_time_limit_s <= 86_400, "max_time_limit_s must be in (0, 86400]"),
            (1 <= self.max_attempts <= 3, "max_attempts must be 1..3"),
            (1 <= self.max_spawn_failures <= 10, "max_spawn_failures must be 1..10"),
            (0.1 <= self.shutdown_grace_s <= 60, "shutdown_grace_s must be 0.1..60"),
        )
        for ok, msg in checks:
            if not ok:
                raise ValueError(f"INVALID_POOL_CONFIG: {msg}")


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    worker_id: str
    outcome: str
    detail: str
    seconds: float


@dataclass(frozen=True)
class JobInfo:
    execution_id: str
    request_id: str
    state: PoolJobState
    reason: str
    worker_id: str | None
    attempts: tuple[AttemptRecord, ...]
    queue_wait_s: float | None
    run_s: float | None
    total_s: float | None


@dataclass(frozen=True)
class WorkerSnapshot:
    worker_id: str
    slot: int
    pid: int | None
    child_pid: int | None  # the interpreter that runs HiGHS (differs from pid behind a launcher)
    state: WorkerState
    jobs_done: int
    startup_s: float | None
    history: tuple[str, ...]


@dataclass(frozen=True)
class PoolStats:
    max_workers: int
    queue_depth: int
    running: int
    workers: dict[str, int]
    counters: dict[str, int]
    startup_s: tuple[float, ...]
    queue_wait_s: tuple[float, ...]
    run_s: tuple[float, ...]


@dataclass(frozen=True)
class ShutdownReport:
    drained: bool
    cancelled_jobs: int
    killed_workers: int
    unterminated_pids: tuple[int, ...]


class _Job:
    def __init__(self, execution_id: str, seq: int, request: WorkerRequest, payload: bytes) -> None:
        self.execution_id, self.seq, self.request, self.payload = (
            execution_id,
            seq,
            request,
            payload,
        )
        self.state = J.QUEUED
        self.reason = ""
        self.response: WorkerResponse | None = None
        self.done = threading.Event()
        self.submitted = time.perf_counter()
        self.admitted: float | None = None
        self.started: float | None = None
        self.finished: float | None = None
        self.deadline = 0.0
        self.worker_id: str | None = None
        self.attempts: list[AttemptRecord] = []
        self.attempt_no = 0
        self.attempt_started = 0.0

    def move(self, new: PoolJobState) -> None:
        if new not in JOB_TRANSITIONS[self.state]:
            raise IllegalTransitionError(
                f"job {self.execution_id}: {self.state.value} -> {new.value}"
            )
        self.state = new

    def info(self) -> JobInfo:
        wait = (self.admitted - self.submitted) if self.admitted is not None else None
        run = (
            ((self.finished or time.perf_counter()) - self.started)
            if self.started is not None
            else None
        )
        total = (self.finished - self.submitted) if self.finished is not None else None
        return JobInfo(
            self.execution_id, self.request.request_id, self.state, self.reason, self.worker_id,
            tuple(self.attempts), wait, run, total,
        )  # fmt: skip


class _Worker:
    def __init__(self, worker_id: str, slot: int) -> None:
        self.worker_id, self.slot = worker_id, slot
        self.state = W.CREATED
        self.history: list[str] = [W.CREATED.value]
        self.proc: subprocess.Popen[bytes] | None = None
        self.job_obj: WindowsJobObject | None = None
        self.child_pid: int | None = None
        self.job: _Job | None = None
        self.jobs_done = 0
        self.kill_reason: str | None = None
        self.deadline: float | None = None  # startup deadline while CREATED
        self.created = time.perf_counter()
        self.startup_s: float | None = None

    def move(self, new: WorkerState) -> None:
        if new not in WORKER_TRANSITIONS[self.state]:
            raise IllegalTransitionError(
                f"worker {self.worker_id}: {self.state.value} -> {new.value}"
            )
        self.state = new
        self.history.append(new.value)
        del self.history[:-_HISTORY]

    def snapshot(self) -> WorkerSnapshot:
        return WorkerSnapshot(
            self.worker_id, self.slot, self.proc.pid if self.proc else None, self.child_pid,
            self.state, self.jobs_done, self.startup_s, tuple(self.history),
        )  # fmt: skip


@dataclass
class _Slot:
    index: int
    worker: _Worker | None = None
    job: _Job | None = None
    dead: bool = False
    spawn_failures: int = 0
    spawns: int = 0
    thread: threading.Thread | None = None


class JobHandle:
    """Caller-side view of one submitted job."""

    def __init__(self, pool: SolverPool, job: _Job) -> None:
        self._pool, self._job = pool, job

    @property
    def execution_id(self) -> str:
        return self._job.execution_id

    @property
    def state(self) -> PoolJobState:
        with self._pool._lock:
            return self._job.state

    def done(self) -> bool:
        return self._job.done.is_set()

    def info(self) -> JobInfo:
        with self._pool._lock:
            return self._job.info()

    def result(self, timeout: float | None = None) -> WorkerResponse:
        """Block until the job is terminal; ``TimeoutError`` if ``timeout`` elapses first."""
        if not self._job.done.wait(timeout):
            raise TimeoutError(f"job {self._job.execution_id} still {self.state.value}")
        assert self._job.response is not None  # noqa: S101 - set before done
        return self._job.response

    def cancel(self) -> bool:
        """Cancel before or during execution. A running job's worker is killed and replaced.
        Returns True when the job ended CANCELLED (False when it was already terminal)."""
        return self._pool._cancel(self._job)


def _synth(
    request: WorkerRequest,
    status: WorkerStatus,
    termination: SolverTermination,
    raw: str,
    code: WorkerFailureCode | None,
    message: str = "",
    runtime: float = 0.0,
) -> WorkerResponse:
    return WorkerResponse(
        contract_version=request.contract_version, request_id=request.request_id,
        model_sha256=request.model_sha256, ir_sha256=request.ir_sha256, status=status,
        termination=termination, raw_solver_status=raw, solver_name="HiGHS",
        solver_version="unknown", runtime_seconds=runtime,
        failure=WorkerFailure(code=code, message=message[:MAX_RAW_TEXT] or raw) if code else None,
    )  # fmt: skip


def _state(job: _Job) -> PoolJobState:
    """Current state through a call, so type narrowing never assumes it is unchanged."""
    return job.state


def _kill_pid(pid: int) -> None:
    # SIGTERM maps to TerminateProcess on Windows; SIGKILL exists only on POSIX
    posix_kill = getattr(signal, "SIGKILL", signal.SIGTERM)
    sig = signal.SIGTERM if sys.platform == "win32" else posix_kill
    with contextlib.suppress(OSError, ProcessLookupError, PermissionError):
        os.kill(pid, sig)


def pid_alive(pid: int) -> bool:
    """Is an OS process with this pid still running? (Windows: exit-code probe; POSIX: signal 0.)"""
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and (
                code.value == 259  # STILL_ACTIVE
            )
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SolverPool(SolverAdapter):
    """Bounded concurrent execution pool implementing the ``SolverAdapter`` protocol."""

    def __init__(self, config: PoolConfig | None = None) -> None:
        self.config = config or PoolConfig()
        self._lock = threading.RLock()
        self._cv = threading.Condition(self._lock)
        self._queue: deque[_Job] = deque()
        self._slots = [_Slot(i) for i in range(self.config.max_workers)]
        self._rr = 0
        self._stopping = False  # no new submissions
        self._closing = False  # runners exit when idle
        self._closed = False
        self._started = False
        self._wd_stop = False
        self._watchdog: threading.Thread | None = None
        self._seq = itertools.count(1)
        self._wid = itertools.count(1)
        self._counters: Counter[str] = Counter()
        self._startup: deque[float] = deque(maxlen=_SAMPLES)
        self._waits: deque[float] = deque(maxlen=_SAMPLES)
        self._runs: deque[float] = deque(maxlen=_SAMPLES)
        self._retired: deque[WorkerSnapshot] = deque(maxlen=256)
        self._all_pids: set[int] = set()
        self._argv = self._build_argv()
        self._env = self._build_env()
        self._atexit = functools.partial(_atexit_shutdown, weakref.ref(self))
        atexit.register(self._atexit)  # per-pool callback: unregistering it affects only this pool
        if self.config.prestart:
            self.start()

    # ------------------------------------------------------------------ SolverAdapter
    @property
    def name(self) -> str:
        return "HiGHS-pool"

    def solve(self, request: WorkerRequest) -> WorkerResponse:
        """Submit and wait. Never raises for solver or pool outcomes; the response says what
        happened (REJECTED / TIMED_OUT / cancelled / failed jobs are never reported as solved)."""
        handle = self.submit(request)
        try:
            return handle.result()
        except BaseException:
            handle.cancel()
            raise

    def __enter__(self) -> SolverPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown(drain=exc[0] is None)

    # ------------------------------------------------------------------ configuration helpers
    def _build_argv(self) -> list[str]:
        if self.config.worker_argv is not None:
            return list(self.config.worker_argv)
        return [
            sys.executable, "-m", "qorvexa.solver_fabric.pool_worker",
            "--memory-limit-bytes", str(self.config.memory_limit_bytes),
        ]  # fmt: skip

    @staticmethod
    def _build_env() -> dict[str, str]:
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
        return env

    # ------------------------------------------------------------------ start / submit
    def start(self) -> None:
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
            self._watchdog = threading.Thread(target=self._watch, name="qsp-watchdog", daemon=True)
            self._watchdog.start()
            for slot in self._slots:
                slot.thread = threading.Thread(
                    target=self._runner, args=(slot,), name=f"qsp-runner-{slot.index}", daemon=True
                )
                slot.thread.start()

    def wait_ready(self, timeout: float = 60.0) -> bool:
        """True once every non-dead worker is READY (useful after ``prestart``)."""
        end = time.perf_counter() + timeout
        with self._cv:
            while True:
                live = [s for s in self._slots if not s.dead]
                if live and all(s.worker is not None and s.worker.state is W.READY for s in live):
                    return True
                left = end - time.perf_counter()
                if left <= 0 or not live:
                    return False
                self._cv.wait(min(left, 0.05))

    def submit(self, request: WorkerRequest) -> JobHandle:
        """Queue a job; never blocks. A REJECTED job already carries its failure response."""
        try:
            payload = request.to_json_bytes()
        except Exception as exc:  # pragma: no cover - defensive; the model is already validated
            payload = b""
            problem = f"request cannot be serialised: {exc}"
        else:
            problem = ""
        if not self._started:
            self.start()
        seq = next(self._seq)
        job = _Job(f"exec-{os.getpid()}-{seq:08d}", seq, request, payload)
        with self._cv:
            self._counters["submitted"] += 1
            reject = problem or self._reject_reason_locked(request, payload)
            if reject:
                self._reject_locked(job, reject)
                return JobHandle(self, job)
            self._queue.append(job)
            self._dispatch_locked()
            if job.state is J.QUEUED and len(self._queue) > self.config.max_queue:
                self._queue.remove(job)  # it is the tail: FIFO order of the others is untouched
                self._reject_locked(job, "POOL_SATURATED: workers busy and queue full")
        return JobHandle(self, job)

    def _reject_reason_locked(self, request: WorkerRequest, payload: bytes) -> str:
        if self._stopping or self._closed:
            return "POOL_SHUTDOWN: the pool is not accepting jobs"
        if len(payload) > MAX_FRAME_BYTES:
            return "REQUEST_TOO_LARGE: request exceeds the IPC frame cap"
        if request.options.time_limit_seconds > self.config.max_time_limit_s:
            return "TIME_LIMIT_EXCEEDS_POOL_CAP: request time limit above max_time_limit_s"
        if self._started and all(s.dead for s in self._slots):
            return "POOL_UNAVAILABLE: no worker could be started"
        return ""

    def _reject_locked(self, job: _Job, reason: str) -> None:
        job.state = J.REJECTED
        job.reason = reason
        job.finished = time.perf_counter()
        job.response = _synth(
            job.request, WorkerStatus.FAILED, SolverTermination.NOT_RUN, reason.split(":")[0],
            WorkerFailureCode.UNAVAILABLE, reason,
        )  # fmt: skip
        self._counters["rejected"] += 1
        job.done.set()

    # ------------------------------------------------------------------ dispatch (lock held)
    def _dispatch_locked(self) -> None:
        if all(s.dead for s in self._slots) and self._started:
            self._fail_queue_locked("POOL_UNAVAILABLE: every worker slot is dead")
            return
        while self._queue:
            slot = self._next_idle_slot_locked()
            if slot is None:
                return
            job = self._queue.popleft()
            job.move(J.ADMITTED)
            job.admitted = time.perf_counter()
            job.worker_id = slot.worker.worker_id if slot.worker else None
            slot.job = job
            self._cv.notify_all()

    def _next_idle_slot_locked(self) -> _Slot | None:
        n = len(self._slots)
        order = [(self._rr + k) % n for k in range(n)]
        for pass_ready in (True, False):  # prefer workers that are READY right now
            for i in order:
                s = self._slots[i]
                if s.job is not None or s.dead or s.thread is None:
                    continue
                ready = s.worker is not None and s.worker.state is W.READY
                if ready == pass_ready:
                    self._rr = (i + 1) % n
                    return s
        return None

    def _fail_queue_locked(self, reason: str) -> None:
        while self._queue:
            self._finish_locked(
                self._queue.popleft(), J.FAILED, reason, WorkerFailureCode.UNAVAILABLE
            )

    # ------------------------------------------------------------------ job completion (lock held)
    def _finish_locked(
        self,
        job: _Job,
        state: PoolJobState,
        reason: str,
        code: WorkerFailureCode | None = None,
        *,
        response: WorkerResponse | None = None,
        termination: SolverTermination = SolverTermination.NOT_RUN,
        raw: str | None = None,
    ) -> None:
        if job.state in TERMINAL_JOB_STATES:
            return
        job.move(state)
        job.reason = reason
        job.finished = time.perf_counter()
        run = (job.finished - job.started) if job.started is not None else 0.0
        if response is None:
            status = WorkerStatus.TIMED_OUT if state is J.TIMED_OUT else WorkerStatus.FAILED
            response = _synth(
                job.request, status, termination, raw or state.value,
                None if state is J.TIMED_OUT else (code or WorkerFailureCode.OTHER), reason, run,
            )  # fmt: skip
        job.response = response
        self._counters[state.value.lower()] += 1
        if job.admitted is not None:
            self._waits.append(job.admitted - job.submitted)
        if job.started is not None:
            self._runs.append(run)
        job.done.set()
        self._cv.notify_all()

    def _record_attempt(self, job: _Job, worker: _Worker | None, outcome: str, detail: str) -> None:
        job.attempts.append(
            AttemptRecord(
                job.attempt_no, worker.worker_id if worker else "-", outcome, detail[:300],
                time.perf_counter() - job.attempt_started,
            )
        )  # fmt: skip

    # ------------------------------------------------------------------ cancellation
    def _cancel(self, job: _Job) -> bool:
        with self._cv:
            if job.state in TERMINAL_JOB_STATES:
                return job.state is J.CANCELLED
            reason = "cancelled by caller"
            if job.state is J.QUEUED:
                with contextlib.suppress(ValueError):
                    self._queue.remove(job)
            elif job.state is J.ADMITTED:
                for s in self._slots:
                    if s.job is job:
                        s.job = None
            elif job.state is J.RUNNING:
                w = next((s.worker for s in self._slots if s.worker and s.worker.job is job), None)
                self._record_attempt(job, w, "CANCELLED", reason)
                if w is not None:
                    self._kill_worker_locked(w, "CANCELLED")
            self._finish_locked(
                job, J.CANCELLED, reason, WorkerFailureCode.OTHER,
                termination=SolverTermination.INTERRUPTED, raw="CANCELLED",
            )  # fmt: skip
            self._dispatch_locked()
            return True

    # ------------------------------------------------------------------ watchdog
    def _watch(self) -> None:
        try:
            with self._cv:
                while not self._wd_stop:
                    now = time.perf_counter()
                    nxt = now + 0.1
                    for s in self._slots:
                        w = s.worker
                        if w is None:
                            continue
                        if w.state is W.CREATED and w.deadline is not None:
                            if now >= w.deadline:
                                self._kill_worker_locked(w, "STARTUP_TIMEOUT")
                            else:
                                nxt = min(nxt, w.deadline)
                        elif w.state is W.BUSY and w.job is not None and w.job.state is J.RUNNING:
                            if now >= w.job.deadline:
                                self._timeout_locked(w.job, w)
                            else:
                                nxt = min(nxt, w.job.deadline)
                    self._cv.wait(timeout=max(0.002, nxt - now))
        except BaseException:  # pragma: no cover - watchdog must never die silently
            with self._cv:
                self._counters["watchdog_crash"] += 1

    def _timeout_locked(self, job: _Job, w: _Worker) -> None:
        # Finalize BEFORE killing so that a late result can never win the race.
        self._record_attempt(job, w, "TIMED_OUT", "wall clock exceeded")
        self._finish_locked(
            job, J.TIMED_OUT, "wall-clock limit exceeded; worker killed", None,
            termination=SolverTermination.SUPERVISOR_KILLED, raw="TIMEOUT_SUPERVISOR_KILLED",
        )  # fmt: skip
        self._counters["killed_timeout"] += 1
        self._kill_worker_locked(w, "TIMEOUT")

    def _kill_worker_locked(self, w: _Worker, reason: str) -> None:
        if w.kill_reason is None:
            w.kill_reason = reason
        if w.state in (W.CREATED, W.READY, W.BUSY, W.COMPLETING):
            w.move(W.FAILED)
        self._hard_kill(w)

    @staticmethod
    def _hard_kill(w: _Worker) -> None:
        if w.proc is not None:
            with contextlib.suppress(Exception):
                w.proc.kill()
        if w.job_obj is not None:
            with contextlib.suppress(Exception):
                w.job_obj.terminate(exit_code=1)
        if w.child_pid is not None and (w.proc is None or w.child_pid != w.proc.pid):
            _kill_pid(w.child_pid)  # the interpreter behind a Windows venv launcher

    # ------------------------------------------------------------------ runner threads
    def _runner(self, slot: _Slot) -> None:
        try:
            while not self._should_exit(slot):
                w = slot.worker
                if w is None or w.state in (W.FAILED, W.TERMINATING, W.TERMINATED):
                    self._start_worker(slot)
                    continue
                job = self._take_job(slot)
                if job is not None:
                    self._run_job(slot, job)
        except BaseException as exc:  # a runner bug must fail its job, never hang it
            self._runner_crashed(slot, exc)
        finally:
            self._finish_slot(slot)

    def _should_exit(self, slot: _Slot) -> bool:
        with self._lock:
            return slot.dead or (self._closing and slot.job is None)

    def _take_job(self, slot: _Slot) -> _Job | None:
        with self._cv:
            while True:
                if slot.job is not None:
                    return slot.job
                if self._closing or slot.dead:
                    return None
                w = slot.worker
                if w is None or w.state is not W.READY:
                    return None
                if w.proc is not None and w.proc.poll() is not None:
                    w.kill_reason = w.kill_reason or "EXITED_IDLE"
                    w.move(W.FAILED)
                    self._counters["crashed_idle"] += 1
                    return None
                self._cv.wait(timeout=0.1)

    def _runner_crashed(self, slot: _Slot, exc: BaseException) -> None:
        with self._cv:
            self._counters["runner_crash"] += 1
            job = slot.job
            if job is not None and job.state not in TERMINAL_JOB_STATES:
                self._finish_locked(
                    job, J.FAILED, f"POOL_INTERNAL_ERROR: {type(exc).__name__}: {exc}"[:300],
                    WorkerFailureCode.OTHER,
                )  # fmt: skip
            slot.job = None
            slot.dead = True
            if slot.worker is not None and slot.worker.state not in (W.TERMINATED,):
                with contextlib.suppress(IllegalTransitionError):
                    self._kill_worker_locked(slot.worker, "RUNNER_CRASH")
            self._dispatch_locked()

    def _finish_slot(self, slot: _Slot) -> None:
        w = slot.worker
        if w is not None and w.state is not W.TERMINATED:
            self._terminate_worker(w, graceful=not self._force_kill)

    # ------------------------------------------------------------------ worker process management
    _force_kill = False

    def _start_worker(self, slot: _Slot) -> None:
        old = slot.worker
        if old is not None and old.state is not W.TERMINATED:
            self._terminate_worker(old, graceful=False)
        if slot.spawn_failures:  # exponential back-off between failed spawns, interruptible
            end = time.perf_counter() + min(2.0, 0.05 * 2**slot.spawn_failures)
            with self._cv:
                while time.perf_counter() < end and not self._closing:
                    self._cv.wait(timeout=0.05)
        with self._cv:
            if self._closing or slot.dead:
                return
            w = _Worker(f"w-{next(self._wid):04d}", slot.index)
            w.deadline = time.perf_counter() + self.config.startup_timeout_s
            slot.worker = w
            slot.spawns += 1
            self._counters["spawned"] += 1
            if slot.spawns > 1:
                self._counters["replaced"] += 1
        hello: WorkerHello | None = None
        err = ""
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            job_obj = (
                WindowsJobObject(memory_limit_bytes=self.config.memory_limit_bytes or None)
                if sys.platform == "win32"
                else None
            )
            proc = subprocess.Popen(  # noqa: S603 - argv is trusted configuration, not job data
                self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=self._env, creationflags=creationflags,
            )  # fmt: skip
            with self._cv:
                w.proc, w.job_obj = proc, job_obj
                self._all_pids.add(proc.pid)
            if job_obj is not None:
                job_obj.assign_process(proc)
            assert proc.stdout is not None  # noqa: S101
            kind, _seq, body = read_frame(proc.stdout)
            if kind is FrameKind.FATAL:
                err = body.decode("utf-8", errors="replace")[:300]
            elif kind is not FrameKind.HELLO:
                err = f"expected HELLO, got {kind.name}"
            else:
                hello = WorkerHello.model_validate_json(body)
                if hello.ipc_version != IPC_VERSION or hello.contract_version != CONTRACT_VERSION:
                    err = "worker speaks another IPC/contract version"
                    hello = None
        except (IpcError, OSError, ValueError) as exc:
            err = err or f"{type(exc).__name__}: {exc}"[:300]
        with self._cv:
            if hello is not None and w.state is W.CREATED and w.kill_reason is None:
                w.child_pid = hello.pid
                self._all_pids.add(hello.pid)
                w.startup_s = time.perf_counter() - w.created
                self._startup.append(w.startup_s)
                w.deadline = None
                w.move(W.READY)
                slot.spawn_failures = 0
                self._dispatch_locked()
                self._cv.notify_all()
                return
            slot.spawn_failures += 1
            self._counters["spawn_failures"] += 1
            if w.state is W.CREATED:
                w.kill_reason = w.kill_reason or f"SPAWN_FAILED: {err}"
                w.move(W.FAILED)
            self._hard_kill(w)
            if slot.spawn_failures >= self.config.max_spawn_failures:
                slot.dead = True
                self._counters["dead_slots"] += 1
                if slot.job is not None and slot.job.state is J.ADMITTED:
                    slot.job.move(J.QUEUED)
                    self._queue.appendleft(slot.job)
                    slot.job = None
                self._dispatch_locked()
            self._cv.notify_all()

    def _terminate_worker(self, w: _Worker, *, graceful: bool) -> None:
        """Bring a worker to TERMINATED: ask politely (optional), kill if needed, close handles."""
        with self._cv:
            if w.state is W.TERMINATED:
                return
            if w.state is not W.TERMINATING:
                if w.state is W.FAILED or w.state in (W.READY, W.CREATED):
                    w.move(W.TERMINATING)
                else:
                    w.move(W.FAILED)
                    w.move(W.TERMINATING)
        proc = w.proc
        if proc is not None:
            if (
                graceful
                and proc.poll() is None
                and w.kill_reason is None
                and proc.stdin is not None
            ):
                with contextlib.suppress(Exception):
                    write_frame(proc.stdin, FrameKind.SHUTDOWN, 0)
                with contextlib.suppress(Exception):
                    proc.wait(timeout=self.config.shutdown_grace_s)
            if proc.poll() is None:
                self._hard_kill(w)
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
            if proc.poll() is None:  # pragma: no cover - last resort
                _kill_pid(proc.pid)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()
        if w.job_obj is not None:
            with contextlib.suppress(Exception):
                w.job_obj.close()
        with self._cv:
            w.move(W.TERMINATED)
            self._retired.append(w.snapshot())
            self._cv.notify_all()

    # ------------------------------------------------------------------ running one job
    def _run_job(self, slot: _Slot, job: _Job) -> None:
        w = slot.worker
        assert w is not None and w.proc is not None  # noqa: S101
        proc = w.proc
        with self._cv:
            if job.state is not J.ADMITTED:  # cancelled in the meantime
                if slot.job is job:
                    slot.job = None
                return
            if w.state is not W.READY or proc.poll() is not None:
                job.move(J.QUEUED)  # un-admit: its worker died before the request was sent
                self._queue.appendleft(job)
                slot.job = None
                if w.state is W.READY:
                    w.kill_reason = w.kill_reason or "EXITED_IDLE"
                    w.move(W.FAILED)
                self._dispatch_locked()
                return
            job.move(J.RUNNING)
            job.started = time.perf_counter()
            job.attempt_started = job.started
            job.attempt_no += 1
            job.worker_id = w.worker_id
            job.deadline = (
                job.started
                + job.request.options.time_limit_seconds
                + (self.config.timeout_buffer_s)
            )
            w.move(W.BUSY)
            w.job = job
            self._cv.notify_all()
        problem: str | None = None
        response: WorkerResponse | None = None
        fatal = ""
        try:
            assert proc.stdin is not None and proc.stdout is not None  # noqa: S101
            write_frame(proc.stdin, FrameKind.SOLVE, job.seq, job.payload)
            kind, seq, body = read_frame(proc.stdout)
            if kind is FrameKind.FATAL:
                fatal = body.decode("utf-8", errors="replace")[:500]
                problem = "WORKER_FATAL"
            elif kind is not FrameKind.RESULT or seq != job.seq:
                problem = "UNEXPECTED_FRAME"
            else:
                with self._lock:
                    if _state(job) is J.RUNNING:
                        w.move(W.COMPLETING)
                response, problem = self._validate(job, body)
        except FrameCorruptError as exc:  # the worker broke the protocol (we kill it; not a crash)
            problem = f"PROTOCOL_VIOLATION: {exc}"[:300]
        except (IpcError, OSError, ValueError) as exc:  # stream closed: the process went away
            problem = f"IPC_FAILURE: {type(exc).__name__}: {exc}"[:300]
        recycle = False
        with self._cv:
            if _state(job) is not J.RUNNING:
                # timed out / cancelled while we waited: the watchdog or cancel() already
                # finalised the job and killed the worker; a late result is discarded.
                slot.job = None
                w.job = None
                self._counters["late_results_discarded"] += 1 if response is not None else 0
            elif problem is None and response is not None:
                self._record_attempt(job, w, "COMPLETED", "")
                self._finish_locked(
                    job, J.COMPLETED, "response accepted at IPC level", response=response
                )
                w.jobs_done += 1
                w.job = None
                slot.job = None
                if w.jobs_done >= self.config.max_jobs_per_worker:
                    w.move(W.TERMINATING)
                    recycle = True
                    self._counters["recycled"] += 1
                else:
                    w.move(W.READY)
                self._dispatch_locked()
            else:
                self._worker_failed_locked(slot, job, w, problem or "UNKNOWN", fatal)
        if recycle:
            self._terminate_worker(w, graceful=True)

    def _validate(self, job: _Job, body: bytes) -> tuple[WorkerResponse | None, str | None]:
        try:
            resp = WorkerResponse.from_json_bytes(body)
        except Exception as exc:
            return None, f"MALFORMED_RESPONSE: {exc}"[:300]
        req = job.request
        if (
            resp.request_id != req.request_id
            or resp.model_sha256 != req.model_sha256
            or resp.ir_sha256 != req.ir_sha256
        ):
            return None, "BINDING_MISMATCH: response ids or hashes differ from the request"
        extra = {
            "pool_version": POOL_VERSION, "pool_execution_id": job.execution_id,
            "pool_worker_id": job.worker_id or "", "pool_attempt": str(job.attempt_no),
            "pool_queue_wait_ms": f"{((job.admitted or job.submitted) - job.submitted) * 1000:.1f}",
        }  # fmt: skip
        merged = {**resp.diagnostics, **extra}
        if len(merged) > 50:
            return resp, None
        return resp.model_copy(update={"diagnostics": merged}), None

    def _worker_failed_locked(
        self, slot: _Slot, job: _Job, w: _Worker, problem: str, fatal: str
    ) -> None:
        """Unexpected worker death or protocol fault while a job was RUNNING (lock held)."""
        rc: int | None = None
        if w.proc is not None:
            with contextlib.suppress(Exception):
                self._hard_kill(w)
                rc = w.proc.wait(timeout=5)
        reason = w.kill_reason
        if w.state in (W.CREATED, W.READY, W.BUSY, W.COMPLETING):
            w.kill_reason = w.kill_reason or problem.split(":")[0]
            w.move(W.FAILED)
        oom = (
            rc in (-1073741756, 3221225540, 137)
            or "memory" in fatal.lower()
            or "bad_alloc" in fatal
        )
        if problem.startswith("IPC_FAILURE") and rc not in (None, 0) and not fatal:
            problem = f"WORKER_EXITED: exit code {rc}"
        self._counters["worker_failures"] += 1
        detail = (
            f"{problem}"
            + (f" [{fatal}]" if fatal else "")
            + (f" exit={rc}" if rc is not None else "")
        )
        self._record_attempt(job, w, "WORKER_FAILED", detail)
        w.job = None
        slot.job = None
        retryable = (
            reason is None
            and problem.startswith(("WORKER_EXITED", "IPC_FAILURE"))  # crash / IPC only
            and job.attempt_no < self.config.max_attempts
            and not self._closing
            and not all(s.dead for s in self._slots)
        )
        if retryable:
            job.move(J.QUEUED)
            self._queue.appendleft(job)
            self._counters["retried"] += 1
        else:
            code = (
                WorkerFailureCode.MEMORY_LIMIT if oom
                else WorkerFailureCode.CRASHED if problem.startswith(_DEATH)
                else WorkerFailureCode.INVALID_REQUEST if problem.startswith("BINDING_MISMATCH")
                else WorkerFailureCode.SOLVER_ERROR
            )  # fmt: skip
            term = (
                SolverTermination.MEMORY_LIMIT if oom
                else SolverTermination.CRASHED if code is WorkerFailureCode.CRASHED
                else SolverTermination.OTHER
            )  # fmt: skip
            self._finish_locked(
                job, J.FAILED, detail, code, termination=term,
                raw=f"EXIT_{rc}" if rc is not None else problem.split(":")[0],
            )  # fmt: skip
        self._dispatch_locked()

    # ------------------------------------------------------------------ introspection
    def stats(self) -> PoolStats:
        with self._lock:
            states = Counter(s.worker.state.value for s in self._slots if s.worker is not None)
            return PoolStats(
                max_workers=self.config.max_workers, queue_depth=len(self._queue),
                running=sum(1 for s in self._slots if s.worker and s.worker.job is not None),
                workers=dict(states), counters=dict(self._counters),
                startup_s=tuple(self._startup), queue_wait_s=tuple(self._waits),
                run_s=tuple(self._runs),
            )  # fmt: skip

    def worker_snapshots(self, *, include_retired: bool = False) -> list[WorkerSnapshot]:
        with self._lock:
            live = [s.worker.snapshot() for s in self._slots if s.worker is not None]
            return [*self._retired, *live] if include_retired else live

    def known_pids(self) -> tuple[int, ...]:
        """Every worker process id this pool ever started (launcher and interpreter)."""
        with self._lock:
            return tuple(sorted(self._all_pids))

    # ------------------------------------------------------------------ shutdown
    def shutdown(self, *, drain: bool = True, timeout: float = 30.0) -> ShutdownReport:
        """Stop the pool. ``drain=True`` lets queued and running jobs finish (until ``timeout``,
        then forces); ``drain=False`` cancels queued jobs and kills running ones immediately."""
        cancelled = killed = 0
        with self._cv:
            if self._closed:
                return ShutdownReport(True, 0, 0, ())
            self._stopping = True
            if not drain:
                cancelled += self._cancel_all_locked("pool shutdown (forced)")
                killed += self._kill_running_locked("SHUTDOWN")
            self._cv.notify_all()
        drained = True
        if drain:
            end = time.perf_counter() + timeout
            with self._cv:
                while self._queue or any(s.job is not None for s in self._slots):
                    left = end - time.perf_counter()
                    if left <= 0:
                        drained = False
                        break
                    self._cv.wait(min(left, 0.05))
                if not drained:
                    cancelled += self._cancel_all_locked("pool shutdown (drain timeout)")
                    killed += self._kill_running_locked("SHUTDOWN")
        with self._cv:
            self._closing = True
            self._force_kill = not drain or not drained
            self._cv.notify_all()
        for s in self._slots:
            if s.thread is not None:
                s.thread.join(timeout=self.config.shutdown_grace_s + 15)
        with self._cv:
            self._wd_stop = True
            self._cv.notify_all()
        if self._watchdog is not None:
            self._watchdog.join(timeout=5)
        stragglers = 0
        for s in self._slots:  # last resort: nothing may outlive a closed pool
            w = s.worker
            if w is not None and w.state is not W.TERMINATED:
                stragglers += 1
                self._hard_kill(w)
        with self._cv:
            for s in self._slots:
                job = s.job
                if job is not None and job.state not in TERMINAL_JOB_STATES:
                    self._finish_locked(
                        job, J.FAILED, "POOL_SHUTDOWN", WorkerFailureCode.UNAVAILABLE
                    )
                s.job = None
            self._fail_queue_locked("POOL_SHUTDOWN: pool closed")
            self._closed = True
        with contextlib.suppress(Exception):
            atexit.unregister(self._atexit)
        alive = tuple(p for p in sorted(self._all_pids) if pid_alive(p))
        return ShutdownReport(drained, cancelled, killed + stragglers, alive)

    def _cancel_all_locked(self, reason: str) -> int:
        n = 0
        while self._queue:
            self._finish_locked(
                self._queue.popleft(), J.CANCELLED, reason, WorkerFailureCode.OTHER,
                termination=SolverTermination.INTERRUPTED, raw="CANCELLED",
            )  # fmt: skip
            n += 1
        for s in self._slots:
            if s.job is not None and s.job.state is J.ADMITTED:
                self._finish_locked(
                    s.job, J.CANCELLED, reason, WorkerFailureCode.OTHER,
                    termination=SolverTermination.INTERRUPTED, raw="CANCELLED",
                )  # fmt: skip
                s.job = None
                n += 1
        return n

    def _kill_running_locked(self, why: str) -> int:
        n = 0
        for s in self._slots:
            w = s.worker
            if w is not None and w.job is not None and w.job.state is J.RUNNING:
                self._record_attempt(w.job, w, "CANCELLED", "pool shutdown")
                self._finish_locked(
                    w.job, J.CANCELLED, "pool shutdown (forced): running job killed",
                    WorkerFailureCode.OTHER, termination=SolverTermination.INTERRUPTED,
                    raw="CANCELLED",
                )  # fmt: skip
                self._kill_worker_locked(w, why)
                n += 1
        return n


def _atexit_shutdown(ref: weakref.ReferenceType[SolverPool]) -> None:
    pool = ref()
    if pool is not None:
        with contextlib.suppress(Exception):
            pool.shutdown(drain=False, timeout=5)
