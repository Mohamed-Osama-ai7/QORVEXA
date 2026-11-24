"""Run Copilot document retrieval through a scoped advisory capability."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field

from qorvexa.agents import (
    Capability,
    CapabilityRegistry,
    Supervisor,
    TaskContext,
    TaskSpec,
    TaskStatus,
    WorkflowScope,
    WorkflowSpec,
)
from qorvexa.auth.principal import Permission, Principal
from qorvexa.contracts.knowledge import HybridSearchHit
from qorvexa.knowledge.rag import HybridRagIndex

_WORKFLOW: Final = WorkflowSpec(
    workflow_id="copilot-document-evidence",
    version="1",
    tasks=(TaskSpec(task_id="search-docs", capability_id="search-docs", capability_version="1"),),
)


class _Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class _Evidence(BaseModel):
    items: tuple[HybridSearchHit, ...] = Field(max_length=3)


def retrieve_document_evidence(
    knowledge: HybridRagIndex,
    principal: Principal,
    *,
    asset_id: str,
    question: str,
) -> tuple[HybridSearchHit, ...]:
    """Retrieve bounded evidence using scope derived from the caller's asset grant."""
    if not principal.can_access_asset(asset_id):
        raise PermissionError("Copilot document evidence requires an asset grant")

    def search(request: _Question, context: TaskContext) -> _Evidence:
        result = knowledge.search(
            request.question,
            tenant_id=context.scope.tenant_id,
            allowed_asset_ids={asset_id},
            top_k=3,
            mode="hybrid_rrf",
        )
        if any(hit.asset_id != asset_id for hit in result.items):
            raise ValueError("Document evidence scope mismatch")
        return _Evidence(items=tuple(result.items))

    capability = Capability.register(
        "search-docs",
        "1",
        input_model=_Question,
        output_model=_Evidence,
        handler=search,
        required_permissions=frozenset({Permission.ASSET_READ}),
    )
    run = Supervisor(CapabilityRegistry((capability,))).run(
        _WORKFLOW,
        scope=WorkflowScope(tenant_id=principal.tenant_id, asset_id=asset_id),
        inputs={"search-docs": {"question": question}},
        permissions=principal.permissions,
    )
    task = run.tasks[0]
    if task.status is not TaskStatus.SUCCEEDED or task.artifact is None:
        raise RuntimeError("Copilot document evidence retrieval failed")
    return _Evidence.model_validate_json(task.artifact.payload_json, strict=True).items
