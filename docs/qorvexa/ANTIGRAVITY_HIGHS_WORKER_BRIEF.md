# Antigravity brief: isolated HiGHS worker (Phase B implementation)

Workspace: `D:\QORVEXA\core` (backend in `backend\`), branch `feat/qorvexa-eval-harness`.
Do not commit, push, stage, reset, clean, stash, merge or switch branches. Preserve all existing changes.

## Ownership

**Antigravity owns (create/edit only these):**
- `backend/src/qorvexa/solver_fabric/highs_adapter.py` (`HighsAdapter`, the supervisor/client side)
- `backend/src/qorvexa/solver_fabric/highs_worker.py` (child-process entry point) and any IPC helper module, e.g. `highs_ipc.py`
- Worker tests: `backend/tests/test_sf_highs_adapter.py` (+ `test_sf_highs_worker.py` if needed)
- Minimal `backend/pyproject.toml` and `backend/uv.lock` changes adding **`highspy==1.16.0`** (pinned, nothing else; uv.lock must only gain what that pin requires)

**Claude owns (Antigravity must NOT edit):**
`encoding_a.py`, `verifier.py`, `worker_contract.py`, `orchestrator.py`, `solver_fabric/__init__.py`, `canonical_lp.py`, `lp_intake.py`, `raw_ingress.py`, `affine.py`, `exact.py`, `contracts/solver_runtime.py`, and all existing `tests/test_sf_*.py` Phase A/S1-S3 tests.

**Frozen (nobody edits):** `backend/src/qorvexa/contracts/solver_fabric.py`, `backend/tests/test_solver_fabric_contracts.py`.

**If the contract needs a change: STOP and report the issue. Never change it silently.**

## Contract (read-only): `backend/src/qorvexa/solver_fabric/worker_contract.py`, `CONTRACT_VERSION = 1`

Import paths:
```python
from qorvexa.solver_fabric.worker_contract import (
    CONTRACT_VERSION, SolverOptions, LPAlgorithm, LoweredLPPayload,
    WorkerRequest, WorkerResponse, WorkerStatus, WorkerFailure, WorkerFailureCode, SolverAdapter,
)
from qorvexa.contracts.solver_runtime import SolverTermination
```
All DTOs are frozen, `extra="forbid"`, camelCase aliases on the wire, `populate_by_name=True`.

- `SolverOptions`: `time_limit_seconds: float` (default 60, 0 < t <= 86400), `threads: int` (1..256, default 1), `algorithm: LPAlgorithm` (`AUTO|SIMPLEX|IPM`), `presolve: bool` (True), `primal_feasibility_tolerance` / `dual_feasibility_tolerance` (default 1e-7, in (0, 1e-2]), `random_seed: int` (0..2**31-1). No free-form parameters; do not add any.
- `LoweredLPPayload`: `num_variables`, `num_rows`, `cost`, `col_lower`, `col_upper`, `row_lower`, `row_upper`, `row_start` (len rows+1, starts 0), `col_index`, `values` (CSR, row-wise), `offset`. Bounds are `float | None`; **`None` = infinite on that side** (map to `highspy.kHighsInf`). `to_lowered()` / `from_lowered()` convert to/from `LoweredLP`.
- `WorkerRequest`: `request_id`, `model_sha256`, `ir_sha256`, `compiler_version`, `problem`, `options`, `contract_version`. Validation recomputes the IR hash from `problem`. `to_json_bytes()`, `WorkerRequest.from_json_bytes(data)`.
- `WorkerResponse`: echo `contract_version`, `request_id`, `model_sha256`, `ir_sha256` **unchanged**; `status` (`FINISHED|TIMED_OUT|FAILED`), `termination` (`SolverTermination`), `raw_solver_status`, `solver_name`, `solver_version`, `objective_value`, `primal_solution`, `dual_solution`, `farkas_ray`, `primal_ray` (tuples of finite floats or `None`), `iterations`, `runtime_seconds`, `diagnostics: dict[str,str]` (<=50 entries, bounded), `failure: WorkerFailure | None` (required iff `FAILED`). `TIMED_OUT` requires termination `TIME_LIMIT` or `SUPERVISOR_KILLED`. `to_json_bytes()`, `from_json_bytes(data)`.
- `WorkerFailure(code: WorkerFailureCode, message: str)`; codes `INVALID_REQUEST, SOLVER_ERROR, CRASHED, MEMORY_LIMIT, UNAVAILABLE, OTHER`.
- `SolverAdapter` (runtime-checkable Protocol): `name: str` property and `solve(self, request: WorkerRequest) -> WorkerResponse`. The orchestrator consumes exactly this: `LPOrchestrator(adapter, config).solve_json(raw)`.

## Semantics
1. The problem is **minimization**: `min cost^T x + offset`, `row_lower <= A x <= row_upper`, `col_lower <= x <= col_upper`. The compiler already converted maximization; never negate again.
2. `dual_solution` = row multipliers `y` with reduced costs `r = cost - A^T y` (IR row order). Convert HiGHS duals if its sign convention differs and prove it with the analytic test below.
3. The adapter returns **raw candidates only**. Never claim optimality, infeasibility or unboundedness; never produce "evidence". The orchestrator's independent exact verifier decides. Solver status is metadata.
4. Candidate vectors, IR order, finite floats only: `primal_solution` (columns), `dual_solution` (rows), `farkas_ray` (rows, from HiGHS dual ray if available), `primal_ray` (columns, from HiGHS primal ray if available). Use `None` when HiGHS provides none. Never invent or pad values; never replace NaN/inf silently (treat as FAILED SOLVER_ERROR).
5. Normal solver outcomes (infeasible, unbounded, limits) are `FINISHED` responses with the matching `termination`, not exceptions. The adapter **never raises** for solver or internal errors: return `FAILED` + `WorkerFailure`.

## Status mapping (metadata only; native string goes into `raw_solver_status`)
kOptimal->OPTIMAL, kInfeasible->INFEASIBLE, kUnbounded->UNBOUNDED, kUnboundedOrInfeasible->UNBOUNDED_OR_INFEASIBLE, kTimeLimit->TIME_LIMIT, kIterationLimit->ITERATION_LIMIT, kSolveError/kModelError->NUMERICAL_ERROR, kInterrupt->INTERRUPTED, anything else->OTHER. Fill `solver_name="highs"`, `solver_version`, `iterations`, `runtime_seconds`.

## Timeout and failure behavior
- HiGHS `time_limit` = `options.time_limit_seconds`; the supervisor additionally enforces a wall-clock deadline (limit + small grace, e.g. 5 s) and kills the child.
- HiGHS-reported time limit: `status=FINISHED`, `termination=TIME_LIMIT`, any candidates it produced are returned (the verifier may still accept an exact certificate).
- Supervisor kill: `status=TIMED_OUT`, `termination=SUPERVISOR_KILLED`.
- Child crash / non-zero exit / invalid or oversized output: `status=FAILED`, `failure.code=CRASHED` (or `MEMORY_LIMIT` / `SOLVER_ERROR` when known). `highspy` unavailable: `FAILED` + `UNAVAILABLE`. Invalid request: `FAILED` + `INVALID_REQUEST`.

## IPC protocol (required)
Child entry point: `python -m qorvexa.solver_fabric.highs_worker` (use `sys.executable`; no shell).
1. Supervisor writes exactly one UTF-8 JSON document, `WorkerRequest.to_json_bytes()`, to the child's stdin, then closes stdin.
2. Child writes exactly one UTF-8 JSON document, `WorkerResponse.to_json_bytes()`, to stdout and exits 0. Stdout carries nothing else (redirect HiGHS console output off stdout; `log_to_console=False`). stderr is diagnostics only and size-capped.
3. Supervisor reads with a hard size cap, validates with `WorkerResponse.from_json_bytes` and checks the echoed binding fields; any violation is `FAILED`. Environment passed to the child is minimal; no network use; temp files only under a supervisor-owned temp directory that is removed afterwards.
4. One request per child process; no shared state between requests.

## Required tests (`tests/test_sf_highs_adapter.py`)
- Analytic LP `min x1 + 2 x2`, `1 <= x1 + x2 <= 4`, `x in [0,3]^2`: `x=(1,0)`, `y=(1,)`, objective 1; through `LPOrchestrator` it must come out `SOLVED`, tier `GAP_NUMERICAL`.
- A maximization problem through the orchestrator (dual sign convention check): optimum 4 for `max x1 + x2` on the same set.
- Infeasible LP (row `x >= 2`, `x in [0,1]`) -> `INFEASIBLE` via an exact verified certificate; unbounded LP (`min -x`, `x >= 0`, row `x >= 1`) -> `UNBOUNDED` or an honest `UNKNOWN`, never a false claim.
- Free-variable LP and a degenerate LP; report any case where the exact dual bound is -inf (do not hide it).
- Timeout path (kill), crash path, invalid-response path, `highspy`-unavailable path, request hash echo, no-raise guarantee, option mapping.
- Protocol/IPC tests with a stub child.

## Definition of done
`ruff check`, `ruff format --check`, `mypy` clean for your files; all Solver Fabric tests and the full backend suite (including `tests/test_evaluation_leakage.py`) green; frozen M1 files unchanged; then report `WORKER_READY` with changed files, the `highspy==1.16.0` diff summary, test counts, contract concerns, Git status.
