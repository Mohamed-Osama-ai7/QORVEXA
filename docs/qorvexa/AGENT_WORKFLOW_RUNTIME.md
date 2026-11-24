# Advisory workflow runtime

`qorvexa.agents` runs a bounded, versioned task graph in the application process. Trusted application code supplies an immutable capability registry. Each registered capability binds an ID and version to an input schema, an output schema, a handler, and any required read permissions. Workflow submissions refer to these IDs and versions.

The supervisor validates the entire graph before invoking a handler. It executes ready tasks in task-ID order and presents their direct dependencies in the same order. Each dependency artifact carries a tenant and factory scope, a workflow ID and version, a producer task ID, and a SHA-256 digest of its canonical JSON payload. Handlers receive fresh input model instances and can read dependency payloads as independent copies.

The calling service must establish the authorized tenant and factory scope and derive permissions from the authenticated caller. Capability adapters must enforce object-level access within that scope. The runtime gives handlers only their validated inputs and direct dependency outputs. Serialize or expose workflow results only to callers authorized for their scope and payloads.

Invalid input and denied permission prevent a task from invoking its handler. Handler errors and invalid outputs prevent a task from producing an artifact. Failed dependencies block their descendants while independent branches continue. The result records stable failure codes without exception text. The runtime accepts up to 64 tasks, up to 16 dependencies per task, and up to 64 KiB per input or output payload.

`TaskInvoker` is the invocation boundary for execution policies. The default implementation calls the handler synchronously. Timeout, cancellation, retry, audit, persistence, and API integration are subsequent milestones. Registered capabilities must stay advisory. Human approval belongs to the existing governed decision service.

From `backend/`, run `uv run pytest tests/test_agent_runtime.py -q`.
