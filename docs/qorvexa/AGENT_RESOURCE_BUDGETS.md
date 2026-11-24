# Advisory workflow resource budgets

A trusted caller can opt in through `ExecutionPolicy.resource_budgets`. Set both the run ceiling and the ceiling for each logical task. The limits cover model tokens, retrieval calls, solver units, and simulation units. Each dimension accepts integers from zero to 2,147,483,647. Omitted dimensions in a configured policy have a zero allowance.

Call `reserve_task_resources(ResourceAmounts(...))` before starting work. Reserve a token upper bound before submitting a model request. Count each retrieval request and choose a conservative work unit for solver and simulator adapters, such as one solver invocation or one simulation replication. Reserve all dimensions an operation needs in one call. The reservation observes cancellation and the active deadline before granting units. If either ceiling would be exceeded, the call charges none of the requested units and the task fails with `budget_exhausted`. A handler that catches the error cannot publish an output. Dependent tasks are blocked, while independent tasks can use the remaining run budget.

Successful reservations are charged immediately and stay charged if an attempt fails, retries, or uses less than reserved. Limits apply to the logical task across all retries. An idempotent replay returns the stored result without repeating work. Different configured limits are part of the request identity.

Reservations are cooperative. An adapter must reserve before making a call or dispatching work to a thread, and must join any work it starts before returning. The runtime closes the reservation context when the attempt returns. A budget policy has no effect on calls that bypass the reservation API. When budgets are disabled, use `task_checkpoint()` to check cancellation and the deadline before starting expensive work.

From `backend/`, run `uv run pytest tests/test_agent_resource_budgets.py -q`.

## Verified production planning adapter

The factory-scoped planning route accepts a case ID and the digest of a server-owned case revision. A trusted caller resolves the case using the authorized tenant and factory before providing it to the adapter. The digest binds the case scope, problem, twin, and experiment configuration. Prepare this route through `WorkflowRouter.prepare` and run it through `ExecutionService` with server-selected resource budgets. Use the uncached OSCO engine and its standard bounded HiGHS solver for this route.

The adapter reserves one solver unit per configured candidate and one simulation unit per candidate and requested replication. OSCO retains its per-run event limit. The adapter uses one OSCO worker inside the outer workflow, clamps the specialist and simulation time limits to the task deadline, and passes a cooperative cancellation check. An in-flight HiGHS solve is subject to its own worker limit. The outer task discards results returned after cancellation or its deadline.

The adapter publishes evidence digests only after a completed recommendation with strictly admitted, verified math and complete simulation. The evidence is for authorized human review. The authenticated API must establish factory access before exposing the route.

Run `uv run pytest tests/test_agent_planning_adapter.py tests/test_agent_admission_integration.py -q` to exercise the real solver and simulation gates using synthetic inputs.
