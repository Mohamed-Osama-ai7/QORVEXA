# Preparing a verified planning proposal

The internal `prepare_planning_intent()` connector accepts UTF-8 JSON from an upstream interpretation step. It accepts up to four candidates and at most 4,096 bytes. Exactly one candidate can prepare a workflow. The only supported route is `verified-production-planning` version `1`.

```json
{
  "candidates": [
    {
      "workflow_id": "verified-production-planning",
      "workflow_version": "1",
      "arguments": {"selection_key": "reviewed-shift"}
    }
  ]
}
```

The application supplies the authorized factory scope, permissions, and a binding from `(scope, selection_key)` to a pinned `PlanningRequest`. Build that mapping from the cases the caller is allowed to use in the current context. The candidate supplies the selection key alone. The case ID and revision digest come from the binding. Extra fields, duplicate JSON members, and multiple candidates require clarification.

The connector calls the registered router to validate permissions, scope, and the bound intake. It returns either a `PreparedWorkflow` for the exact approved planning template or an `IntentClarification` with a stable reason. An unavailable scope, permission, or case binding gives the same reason. Preparation executes no tasks. The calling application may pass an explicit prepared result to the existing execution service. The workflow returns advisory evidence for human review.

## Explicit planning requests

`prepare_planning_text()` supports a single-line English request such as `Review the production plan for reviewed-shift?`. The command is case-insensitive, and the selection key is matched exactly within the authorized binding. The input limit is 256 ASCII characters. Empty input returns `missing_text`. Other wordings, multiline or compound requests, and invalid input return `unsupported_text`.

The text and structured proposal paths share the same scope, permission, and case checks. The calling service submits the prepared case to `ActorRunService`. The synthetic smoke test runs that path through HiGHS and discrete-event simulation, then checks replay and grant revocation. From `backend/`, run `uv run pytest tests/test_agent_planning_text.py tests/test_agent_planning_smoke.py -q`.
