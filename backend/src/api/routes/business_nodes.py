"""Public TF-WF-010 business node and WorkbenchTask contracts."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.domain.workflow.business_node_service import BusinessNodeService
from src.infra.db.identity_repository import get_session_store
from src.schemas.models import OwnerScope

router = APIRouter(prefix="/api/v1/business-nodes", tags=["business-nodes"])
_service = BusinessNodeService()
_sessions = get_session_store()


class CandidateSetRequest(BaseModel):
    candidate_version_ids: list[UUID]
    failed_candidates: list[dict[str, Any]] = Field(default_factory=list)
    cost_allocation: dict[str, Any] = Field(default_factory=dict)
    run_id: UUID | None = None
    node_run_id: UUID | None = None


class SelectionRequest(BaseModel):
    ranking: list[UUID] = Field(default_factory=list)
    selected_version_ids: list[UUID]
    rubric_revision: str
    rationale: str = ""


class WorkbenchTaskRequest(BaseModel):
    workflow_revision_id: UUID
    run_id: UUID
    node_run_id: UUID
    attempt_id: UUID
    input_snapshot_refs: list[dict[str, Any]] = Field(default_factory=list)
    target_workbench: str
    output_schema_ref: str
    resource_type: str
    expected_draft_version: int = Field(default=0, ge=0)


class WorkbenchSubmitRequest(BaseModel):
    task_version: int = Field(ge=1)
    idempotency_token: str = Field(min_length=8, max_length=255)
    output_artifact_version_ids: list[UUID]
    resource_id: UUID | None = None


def _task(task: Any) -> dict[str, Any]:
    return {
        "task_id": str(task.task_id), "task_kind": task.task_kind,
        "owner_layer": task.owner_layer, "workflow_revision_id": str(task.owner_revision_id),
        "run_id": str(task.run_id), "node_run_id": str(task.node_run_id),
        "status": task.status.value, "schema_ref": task.schema_ref,
        "input_snapshot_refs": task.input_snapshot_refs, "workbench": task.timeout_policy,
        "task_version": task.task_version,
    }


def _actor(authorization: str | None) -> tuple[UUID, OwnerScope]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "UNAUTHORIZED"}})
    try:
        actor_id = _sessions.account_for_token(authorization.removeprefix("Bearer "))
    except (ConflictError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return actor_id, OwnerScope(kind="user", id=actor_id)


@router.get("/catalog")
async def catalog() -> dict[str, Any]:
    return {"nodes": _service.catalog()}


@router.post("/candidate-sets", status_code=201)
async def create_candidate_set(body: CandidateSetRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    try:
        _, owner = _actor(authorization)
        row = _service.create_candidate_set(owner_scope=owner.scoped_id, **body.model_dump())
    except (ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {"candidate_set_id": str(row.candidate_set_id), "candidate_refs": row.candidate_refs, "failed_candidates": row.failed_candidates, "cost_allocation": row.cost_allocation}


@router.post("/candidate-sets/{candidate_set_id}/selections", status_code=201)
async def select_candidate_set(candidate_set_id: UUID, body: SelectionRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    try:
        actor_id, owner = _actor(authorization)
        row = _service.select(candidate_set_id=candidate_set_id, owner_scope=owner.scoped_id,
            actor_or_model=f"user:{actor_id}", **body.model_dump())
    except (ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {"selection_id": str(row.selection_id), "candidate_set_id": str(row.candidate_set_id), "selected_refs": row.selected_refs, "ranking": row.ranking, "rubric_revision": row.rubric_revision}


@router.post("/workbench-tasks", status_code=201)
async def create_workbench_task(body: WorkbenchTaskRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    try:
        _, owner = _actor(authorization)
        return _task(_service.create_workbench_task(owner_scope=owner.scoped_id, **body.model_dump()))
    except (ConflictError, ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/workbench-tasks/{task_id}/submit")
async def submit_workbench_task(task_id: UUID, body: WorkbenchSubmitRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    try:
        actor_id, owner = _actor(authorization)
        commits = _service.submit_workbench_task(task_id=task_id, owner_scope=owner.scoped_id, actor_id=actor_id, **body.model_dump())
    except (ConflictError, ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {"status": "committed", "resource_refs": [{"resource_id": str(row.resource_id), "resource_type": row.resource_type, "revision_id": str(row.revision_id)} for row in commits]}
