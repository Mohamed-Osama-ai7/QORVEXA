# Approved advisory workflow routing

Application code registers a versioned workflow template, its factory or asset scope, and the root task that accepts request arguments. A request selects a workflow ID and supplies arguments that must match that task's schema. Other tasks start with empty arguments and can consume typed artifacts from their dependencies.

`WorkflowRouter` verifies the graph and registered capabilities. It checks the caller's scope kind, the graph's combined capability permissions, and the intake schema before any task starts. The calling service establishes actor authority and the resource ID. The request cannot supply a graph, capability versions, permissions, or scope through its arguments. Trusted callers can prepare the approved template without execution and pass the versioned graph, scope, permissions, and snapshot to `ExecutionService`.

Copilot uses the router for document evidence after its existing caller and asset checks. This is a routing boundary for known workflows. An industrial intent classifier or planner can propose an approved route later through an owner-reviewed integration.

From `backend/`, run `uv run pytest tests/test_agent_routing.py tests/test_copilot.py -q`.
