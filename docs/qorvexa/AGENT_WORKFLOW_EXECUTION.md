# Advisory workflow execution and recovery

`ExecutionService` adds durable run identity and execution controls to the in-process supervisor. The caller supplies an authorized `WorkflowScope`, permission set, and idempotency key. A key is unique within its scope. A canonical digest binds that key to the graph, capability versions, inputs, permissions, and execution policy. Repeating the same request returns its current run state or its previously completed result. A different request with the same key raises `RunConflictError`.

SQLite claims a run and records its start in one transaction. A duplicate submission can observe a running claim without starting additional work. Final results are stored separately from the journal with a content digest for integrity checks. The journal records run and attempt metadata and excludes inputs, outputs, prompts, and exception details. The host application must restrict access to the database file because completed results can contain scoped workflow payloads.

The service bounds attempts and elapsed time at both run and task level. The task deadline spans its attempts and backoff and cannot exceed the remaining run deadline. A handler can call `task_checkpoint()` or `task_remaining_seconds()` to observe a cancellation or deadline and bound a dependency call. The supervisor checks again before accepting a validated output, so a handler that returns late cannot publish that output. Cancellation is cooperative. A non-cooperative synchronous handler may continue occupying its calling thread until it returns.

Retries require both a capability registered with `replay_safe=True` and an adapter raising `ReplaySafeTransientError` for a known retryable outcome. Retries use bounded exponential backoff and stop when the run or task limit is exhausted. A journal failure prevents more handler invocations for that run.

If a worker exits before completing a run, an authorized caller may invoke `recover_expired(scope, key)` after the claim expires. Recovery records the run as interrupted and preserves its metadata. The original key continues to return that terminal state. Recovery never executes a handler, and a late worker cannot replace the interruption with its result.

From `backend/`, run `uv run pytest tests/test_agent_runtime.py tests/test_agent_execution.py -q`.
