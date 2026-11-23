# Solver Fabric — Multi-Worker Concurrent Execution Pool

Code: `backend/src/qorvexa/solver_fabric/pool.py`, `pool_ipc.py`, `pool_worker.py`
Tests: `backend/tests/test_sf_pool.py` (35, real processes, no mocks) + `tests/sf_pool_fault_worker.py` (fault injection)
Benchmark: `backend/benchmarks/solver_pool/bench_solver_pool.py`, results in `backend/benchmarks/solver_pool/results/`

## 1. Starting point (audit, before this milestone)
* `HighsAdapter.solve()` started **one fresh Python worker process per job** (`highs_worker.py`), wrote the request to
  stdin, blocked on `communicate()` with a wall clock of `time_limit + buffer`, and killed the process on timeout.
* There was **no pool, queue, concurrency limit or cancellation**. Concurrent callers could each spawn processes without bound.
* Memory: a Windows Job Object with `JOB_OBJECT_LIMIT_JOB_MEMORY` (default 4 GiB). **No limit on POSIX.**
* Timeout / crash / malformed-output handling was covered by **mocked** tests; one test used a real subprocess.
* `LPOrchestrator` is stateless apart from its injected `SolverAdapter`, and the independent verifier runs after the adapter.
* Each job paid the worker start-up cost (Python + numpy + highspy + qorvexa imports): **≈2 s on the reference machine**.

## 2. Architecture
`SolverPool` implements the existing `SolverAdapter` protocol, so `LPOrchestrator(SolverPool(cfg))` works unchanged and the
verifier remains the only authority on mathematical outcomes. No shared contract was changed.

```
callers ──submit()──► bounded FIFO queue ──dispatch (round-robin over idle READY workers)──► runner thread i ──frames──► worker process i
                                ▲                                                                   │
                         watchdog thread: per-job deadlines, start-up deadlines, kills ◄────────────┘
```

* `max_workers` persistent worker processes (`python -m qorvexa.solver_fabric.pool_worker`), one runner thread each, plus a
  single watchdog thread. Thread count is `max_workers + 1` and never grows. Processes are only started to fill a slot.
* Each job runs on a **fresh `highspy.Highs()`** inside the worker (`solve_with_highs` builds one per call): no solver state
  carries over between jobs. Workers are recycled after `max_jobs_per_worker` jobs to bound slow memory growth.
* Provenance: every accepted response carries `pool_version`, `pool_execution_id`, `pool_worker_id`, `pool_attempt`,
  `pool_queue_wait_ms` in `diagnostics`; the orchestrator records the adapter name `HiGHS-pool`.

### IPC (`pool_ipc.py`)
Frame = `b"QSP1"` | kind (1 B) | sequence (8 B) | length (4 B, ≤ 50 MiB) | JSON body. Kinds: HELLO, SOLVE, RESULT, SHUTDOWN,
BYE, FATAL. Bodies are validated with the strict pydantic contracts (`WorkerRequest`, `WorkerResponse`, `WorkerHello`); there
is no pickle or eval, so a payload can never execute code. The length is checked **before** the body is read. A RESULT is
accepted only when its sequence equals the worker's single outstanding job and its `request_id`, `model_sha256`, `ir_sha256`
match the request. The worker moves the real stdout to a private descriptor and points fd 1/2 at the null device, so native
banners cannot corrupt frames; stderr of workers is not a pipe at all (no pipe can fill and block a worker). Worker-side fatal
errors arrive as one FATAL frame (≤ 4000 bytes) before a non-zero exit.

## 3. Lifecycles (explicit tables; illegal moves raise `IllegalTransitionError`)
Worker: `CREATED → READY → BUSY → COMPLETING → READY` (or `→ TERMINATING` when recycled); any live state `→ FAILED`;
`FAILED → TERMINATING → TERMINATED`. `CREATED → READY` happens only after a valid HELLO (IPC and contract versions match).

Job (`PoolJobState`): `QUEUED → ADMITTED → RUNNING → COMPLETED | FAILED | TIMED_OUT | CANCELLED`; `REJECTED` is terminal and
never queued. Two controlled back-edges: `ADMITTED → QUEUED` when the worker died before the request was sent (no attempt is
consumed), and `RUNNING → QUEUED` for an explicit retry. **`COMPLETED` means "a contract-valid, correctly bound response was
received"; it says nothing about solver success or mathematical validity.**

## 4. Concurrency, dispatch and backpressure
* Bounds: `max_workers` (1–64), `max_queue` (0–10 000 jobs waiting beyond those held by workers).
* `submit()` never blocks. When all workers are busy and the queue is full, the job is **REJECTED** with
  `POOL_SATURATED` and a FAILED / `NOT_RUN` response is available immediately (`solve()` returns it; it never raises).
* Strict FIFO: a job waits only for the jobs ahead of it. The head job goes to the next idle READY worker in round-robin
  order, so idle workers share work and the assignment is deterministic for a given submission and completion order (with
  idle workers and sequential submission, job *i* runs on worker *i mod N*).
* Other rejections: pool shut down, request above the IPC cap, request time limit above `max_time_limit_s`, no live worker.

## 5. Timeouts, cancellation, failures, retries
* Wall clock per job = request `time_limit_seconds` + `timeout_buffer_s`, measured from RUNNING. The watchdog **finalises the
  job as TIMED_OUT first and then kills the worker**, so a result arriving later is discarded. The killed worker is replaced.
* `JobHandle.cancel()`: QUEUED/ADMITTED jobs are removed; a RUNNING job's worker is killed and replaced (CANCELLED,
  termination `INTERRUPTED`).
* Crash (unexpected EOF / exit code) → FAILED, code `CRASHED` (or `MEMORY_LIMIT` for OOM exit codes / memory FATALs).
  Protocol violations (corrupt frame, wrong sequence, malformed JSON) → FAILED `SOLVER_ERROR`; wrong ids/hashes → FAILED
  `INVALID_REQUEST`. The offending worker is killed and replaced. A worker that dies while idle is detected within ~100 ms.
* Start-up: HELLO must arrive within `startup_timeout_s`; after `max_spawn_failures` consecutive failures a slot is declared
  dead (exponential back-off between attempts). With no live slot, queued jobs fail and new ones are rejected
  (`POOL_UNAVAILABLE`): no job can wait forever.
* Retries: off by default (`max_attempts=1`). If enabled (≤ 3) they apply **only to worker crashes and IPC stream failures**,
  never to timeouts, cancellations, protocol violations or solver-reported failures. Every attempt is recorded
  (`JobInfo.attempts`). LP solves are pure functions of the request, so a retried solve has no side effects.
* None of these paths can yield a successful response: synthesized responses never carry primal/dual vectors.

## 6. Resource boundaries (what is enforced, where)
| Control | Windows (verified) | POSIX (implemented, **not verified here**) |
|---|---|---|
| Max concurrent jobs / processes | enforced by `max_workers` | same |
| Queue capacity | enforced (`max_queue`, REJECT) | same |
| Per-job wall clock | enforced (watchdog kill) | same |
| Worker memory | **enforced** by a Job Object per worker (`JobMemoryLimit`); test shows a 900 MiB allocation refused under an 800 MiB limit and accepted without it, and a limit below the worker footprint prevents start-up | `RLIMIT_AS` set by the worker at start (address-space limit, stricter than RSS) |
| Process-tree cleanup | `TerminateJobObject` + kill of the interpreter pid behind the venv launcher; `KILL_ON_JOB_CLOSE` if the parent dies | `kill()`; workers exit on stdin EOF when the parent dies |
| HiGHS threads | `SolverOptions.threads` (default 1) | same |

The idle worker interpreter commits ≈430 MiB of private memory on the reference machine (numpy/BLAS/highspy), so memory limits
must be set above that. CPU time per worker is not capped (the wall clock is). WSL had no Linux distribution installed, so
**no Linux/WSL run was possible**; POSIX code paths are untested.

## 7. Shutdown
`shutdown(drain=True, timeout=30)`: stop accepting; let queued and running jobs finish; on timeout fall back to force.
`shutdown(drain=False)`: cancel queued jobs, kill running ones (CANCELLED). Then: SHUTDOWN frame → wait `shutdown_grace_s` →
kill → close pipes and Job Objects → join threads. `ShutdownReport.unterminated_pids` lists any process still alive
(tests require it to be empty). The pool is a context manager and registers a per-pool `atexit` hook.

## 8. Benchmarks
Script: `backend/benchmarks/solver_pool/bench_solver_pool.py`; raw results: `backend/benchmarks/solver_pool/results/{small,medium}.json`.

**Environment.** Windows 11 (10.0.26200), Python 3.12.10, highspy 1.16.0, Intel64 Family 6 Model 165 (12 logical CPUs),
HiGHS `threads=1` per job. One machine, no other heavy load during the runs.

**Workloads (identical inputs for every configuration).** 16 deterministic sparse LPs per workload (seeds 1000–1015),
`max c^T x` s.t. `A x <= A x0 + 1`, `0 <= x <= 10` (feasible and bounded by construction).
*small*: 200 variables × 100 rows, 8 nonzeros/row (≈4 ms per solve). *medium*: 1200 × 600, 15 nonzeros/row (≈0.2 s per solve).

**Method.** Each configuration was run 5 times; tables show the median (min–max of wall time). Reference objectives come from a
prior in-process solve (which also warms imports for the in-process configuration); every response was checked against them.
Pool numbers are for a **warm** pool (all workers READY, startup excluded); start-up is reported separately and as *cold wall*
(startup + work). Spawn-per-job includes process start-up by construction, since that is what it is.
Configurations: *in-process* = `HighsAdapter(in_process=True)` sequential (no isolation; reference only); *spawn* =
`HighsAdapter()` sequential, one process per job (the pre-pool production path); *pool-wN* = `SolverPool(max_workers=N)` with all
16 jobs submitted at once. An 8-worker run was also recorded in the JSON files.

### Medium workload (16 jobs, ≈0.2 s solve each)
| Configuration | Wall (median, min–max) | Throughput | Latency p50 / p95 | Max queue wait | Cold wall | Speed-up vs spawn | vs in-process |
|---|---|---|---|---|---|---|---|
| in-process (no isolation) | 3.33 s (3.27–3.72) | 4.80 jobs/s | 0.21 / 0.22 s | — | — | 13.3× | 1.00× |
| spawn per job | 44.44 s (42.12–46.27) | 0.36 jobs/s | 2.61 / 3.09 s | — | — | 1.00× | 0.07× |
| pool, 1 worker | 3.62 s (3.53–4.04) | 4.42 jobs/s | 1.99 / 3.36 s | 3.36 s | 5.85 s | 12.3× | 0.92× |
| pool, 2 workers | 1.99 s (1.90–2.08) | 8.03 jobs/s | 1.17 / 1.95 s | 1.65 s | 4.55 s | 22.3× | 1.67× |
| pool, 4 workers | 1.33 s (1.17–1.43) | 12.05 jobs/s | 0.87 / 1.24 s | 1.03 s | 4.57 s | 33.5× | 2.51× |

### Small workload (16 jobs, ≈4 ms solve each)
| Configuration | Wall (median, min–max) | Throughput | Latency p50 / p95 | Max queue wait | Cold wall | Speed-up vs spawn | vs in-process |
|---|---|---|---|---|---|---|---|
| in-process (no isolation) | 0.066 s (0.064–0.072) | 241 jobs/s | 4.2 / 4.6 ms | — | — | 590× | 1.00× |
| spawn per job | 38.93 s (36.10–40.64) | 0.41 jobs/s | 2.42 / 2.67 s | — | — | 1.00× | 0.002× |
| pool, 1 worker | 0.088 s (0.086–0.089) | 181 jobs/s | 49 / 81 ms | 81 ms | 2.42 s | 442× | 0.75× |
| pool, 2 workers | 0.048 s (0.044–0.050) | 335 jobs/s | 28 / 45 ms | 41 ms | 2.46 s | 811× | 1.38× |
| pool, 4 workers | 0.029 s (0.028–0.037) | 557 jobs/s | 20 / 26 ms | 21 ms | 2.93 s | 1342× | 2.28× |

Pool latency is measured from submission (all 16 jobs submitted at once), so it includes queue wait. That is why pool p50
latency exceeds the per-solve time.

**Worker start-up overhead** (spawn → HELLO, median per worker): 2.1–2.3 s with 1 worker, 2.4–2.5 s with 2, and 2.9–3.2 s
with 4. Start-up grows when several workers start at once, because their imports contend for CPU and disk. Peak working set
per worker process was ≈106 MiB (small) and ≈111 MiB (medium). The private commit of an idle worker is ≈430 MiB (section 6).

**Recovery** (3 probes per workload): after an idle worker process was killed externally, the pool detected it in 0.10–0.11 s
and had a replacement READY after 2.15–2.43 s. The next job completed normally.

**What the numbers do and do not show.**
* The large gain over *spawn* comes almost entirely from **not paying ≈2.4 s of process start-up per job**. It is largest for
  small LPs and is a property of persistent workers, not of parallelism.
* Against unisolated in-process solving, one isolated worker costs 8–25 % in IPC and serialisation. With 2 and 4 workers the
  pool is 1.4–2.5× faster than sequential in-process solving on these workloads. Scaling is sub-linear, partly because request
  serialisation and response validation run in the parent under the GIL.
* *Cold* runs include start-up. For a single batch of 16 medium jobs, a cold 4-worker pool (4.57 s) is slower than sequential
  in-process solving (3.33 s). The pool pays off when workers are reused across many jobs.
* This covers one machine, one OS, synthetic LPs and 5 repetitions. It says nothing about Linux, much larger LPs or end-to-end
  orchestrator time (compilation and exact verification are outside the measured path). These results are not a general
  performance claim.

## 9. Remaining limitations
* Linux/WSL not verified on this machine; POSIX memory limit and kill paths are untested.
* Single-host, in-process pool: no distributed queue, no persistence of queued jobs across a parent restart.
* Parent-side work (spec parsing, compilation, exact verification) runs in the caller's threads under the Python GIL; the pool
  parallelises the solver processes, not the verifier.
* `max_queue` rejection is the only saturation policy (no blocking submit); callers decide whether to retry later.
* Worker start-up is ≈2 s each; a timed-out or cancelled job costs a worker replacement.
* Retries are limited to crashes / IPC stream failures by design.
