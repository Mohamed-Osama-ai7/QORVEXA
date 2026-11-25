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
