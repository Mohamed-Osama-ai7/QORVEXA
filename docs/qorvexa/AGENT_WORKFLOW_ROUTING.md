# Approved advisory workflow routing

Application code registers a versioned workflow template, its factory or asset scope, and the root task that accepts request arguments. A request selects a workflow ID and supplies arguments that must match that task's schema. Other tasks start with empty arguments and can consume typed artifacts from their dependencies.

`WorkflowRouter` verifies the graph and registered capabilities. It checks the caller's scope kind, the graph's combined capability permissions, and the intake schema before any task starts. The calling service establishes actor authority and the resource ID. The request cannot supply a graph, capability versions, permissions, or scope through its arguments. Trusted callers can prepare the approved template without execution and pass the versioned graph, scope, permissions, and snapshot to `ExecutionService`.

Copilot uses the router for document evidence after its existing caller and asset checks. This is a routing boundary for known workflows. An industrial intent classifier or planner can propose an approved route later through an owner-reviewed integration.

From `backend/`, run `uv run pytest tests/test_agent_routing.py tests/test_copilot.py -q`.

## RFC for an authenticated workflow lifecycle

**Status** — Proposed for Member 1 approval under the [contract-first rule](TEAM_WORKSTREAMS.md#5-cross-workstream-integration-rules). Member 5 owns the execution integration. Member 1 owns the public API and authorization contract. Member 6 reviews worker deployment, quotas, and operational readiness.

### Identity and resource scope

Derive actor and tenant IDs from the verified principal. Qualify actor identity by issuer if the service accepts more than one identity issuer. The request may name an asset or factory. The service must resolve that object and its tenant through an authoritative grant before looking up an existing run or allocating any worker. The current principal supports asset grants. Factory routes require an approved identity claim or access-control lookup and a scoped lookup for stored planning inputs. An asset grant alone never establishes access to an entire factory. Start with an asset-scoped endpoint if the factory authority is unavailable.

Bind the durable idempotency namespace to the authenticated actor, tenant, and exact resource. Bind the request digest to the resolved template version, canonical arguments, authorized input revisions, and server-selected execution policy. The same actor and request can retrieve the existing run after reauthorization. A changed request in that namespace returns `409`. Different actors may use the same client key independently. Legacy internal runs have no actor ownership and require an explicit migration policy before they can be exposed.

Authorize each read or mutation against the current grant. An actor can access their own runs within that grant. Any reviewer access needs an explicit role and resource-level permission. Filter listing and artifact lookups at storage, using the authorized scope in the query. Return the same `404` for an absent run and a run outside the caller's allowed scope.

### Proposed public operations

| Operation | Request and behavior |
| --- | --- |
| `POST /api/v1/advisory-workflows/runs` | Accept an approved template ID, one resource reference, typed template arguments, and an idempotency key. Return `202` with an opaque, durable run ID after an atomic claim and queue transition. Return `200` with the existing run for a duplicate with the same intent. |
| `GET /api/v1/advisory-workflows/runs` | Return bounded, paginated summaries from the authorized actor and resource scope. |
| `GET /api/v1/advisory-workflows/runs/{run_id}` | Return status, timestamps, stable error or block reasons, and authorized evidence summaries with their provenance. |
| `POST /api/v1/advisory-workflows/runs/{run_id}/cancel` | Record an idempotent cancellation request for an authorized live run. Return its current terminal state when completion won the race. |

Keep capability IDs, graphs, permissions, and policy limits server-owned. Result summaries include template version, evidence digests, and whether evidence came from simulation. Keep raw prompts, arguments, and internal exception messages out of public status and audit metadata. Treat detailed evidence retrieval as a separately authorized read. A result is advisory. Operational approval stays in the governed human decision flow.

### Worker and recovery behavior

The proposed public states are `queued`, `running`, `cancelling`, `succeeded`, `failed`, `cancelled`, and `interrupted`. Claim queued work atomically and pin the admitted template and input revisions from enqueue through execution. A run succeeds only when the template's required tasks produce verified results. Failed or blocked required tasks cause failure even when other branches finish. Enforce a deployment-wide worker limit as well as a per-run task limit. Apply a bounded queue and per-tenant limits before allocating resources, including for bursts that use unique keys. Return `429` for tenant limits or `503` for unavailable global capacity, with a bounded `Retry-After` hint. The worker observes the durable cancellation request at checkpoints. Record `cancelling` until the worker confirms termination. Use a lease or equivalent fencing token and check it on every terminal write so an expired worker cannot publish a result after recovery.

Record terminal state and result metadata together. A worker that loses its lease leaves an interrupted run for authorized review. A restart or retry of an interrupted run requires an explicit new run linked to the original and a check that the selected workflow may be replayed. This design keeps retry policy for known repeatable work separate from recovery after an ambiguous interruption.

### Decisions for approval

1. Confirm the authoritative factory grant source and the roles allowed to start, inspect, and cancel each initial template.
2. Agree on actor ownership, treatment of legacy internal records, and whether reviewer access is part of the first release.
3. Choose the worker and queue deployment model, tenant limits, artifact retention, and the initial staging environment with Member 6.

### Acceptance for the first staging run

- Distinct actors and tenants cannot access each other's runs, evidence, or cancellation operations. Identical submissions for one actor create one run. Changed arguments with the same key produce a conflict.
- Saturation returns a bounded admission response. A cancellation or expired lease cannot publish a late result. Queued cancellation and worker-issued terminal updates are race-tested.
- A real, reproducible scoped workflow links inputs, verification and simulation evidence, and the resulting review summary. Failed verification blocks simulation. The human approval action remains separate.
- Approve the OpenAPI contract, regenerate the client, and pass the route allowlist, access-control tests, full CI, and one authenticated staging smoke test.

### Staging smoke procedure

Use the staging identity provider and synthetic fixtures for two tenants, a requester and a separately authorized reviewer, plus an actor without the target resource grant. Record the deployment commit, template version, and staged input revision. Keep credentials in the team's staging secret store.

1. Submit the pinned synthetic case twice as the same requester and compare the run IDs. Reuse the key with changed arguments and confirm that it returns a conflict. Submit as a different authorized actor and confirm that they receive their own run.
2. Check status and evidence as the requester, reviewer if reviewer access is enabled, and actor outside the grant. Confirm storage-side filtering for listings and for evidence fetched by ID. Remove the requester's grant and confirm that later requests cannot retrieve or cancel the run.
3. For a successful plan, record the template version and input, math verification, simulation, and review artifact digests. Confirm that the output still requires a human decision. Exercise an unverified solver result and an incomplete simulation with the existing synthetic cases.
4. Cancel a queued run and a running run. Confirm that cancelled and interrupted runs remain terminal, with no late artifact publication. Exceed the staged queue and tenant capacities, then verify that new submissions get the corresponding admission response and that accepted work still drains.

Use `tests/test_agent_planning_adapter.py` and `tests/test_agent_admission_integration.py` for the pre-deployment reference run. Keep the staging run IDs and redacted verification evidence with the release record.
