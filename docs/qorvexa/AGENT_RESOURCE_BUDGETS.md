# Advisory workflow resource budgets

A trusted caller can opt in through `ExecutionPolicy.resource_budgets`. Set both the run ceiling and the ceiling for each logical task. The limits cover model tokens, retrieval calls, solver units, and simulation units. Each dimension accepts integers from zero to 2,147,483,647. Omitted dimensions in a configured policy have a zero allowance.

Call `reserve_task_resources(ResourceAmounts(...))` before starting work. Reserve a token upper bound before submitting a model request. Count each retrieval request and choose a conservative work unit for solver and simulator adapters, such as one solver invocation or one simulation replication. Reserve all dimensions an operation needs in one call. The reservation observes cancellation and the active deadline before granting units. If either ceiling would be exceeded, the call charges none of the requested units and the task fails with `budget_exhausted`. A handler that catches the error cannot publish an output. Dependent tasks are blocked, while independent tasks can use the remaining run budget.

Successful reservations are charged immediately and stay charged if an attempt fails, retries, or uses less than reserved. Limits apply to the logical task across all retries. An idempotent replay returns the stored result without repeating work. Different configured limits are part of the request identity.

Reservations are cooperative. An adapter must reserve before making a call or dispatching work to a thread, and must join any work it starts before returning. The runtime closes the reservation context when the attempt returns. A budget policy has no effect on calls that bypass the reservation API. When budgets are disabled, use `task_checkpoint()` to check cancellation and the deadline before starting expensive work.

From `backend/`, run `uv run pytest tests/test_agent_resource_budgets.py -q`.
