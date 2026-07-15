"""Owner-confirmed Workflow Architect proposal API."""

from __future__ import annotations
from typing import Any
from uuid import UUID
from uuid import uuid4
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.domain.agent.architect_service import ArchitectService
from src.api.auth import require_owner
from src.core.config import settings
from src.domain.workflow.sql_workflow_service import SqlWorkflowService

router = APIRouter(prefix="/api/v1/architect", tags=["architect"])
_architect = ArchitectService()


def _owned_proposal(proposal_id: UUID, authorization: str | None) -> dict[str, Any]:
    proposal = _architect.latest(proposal_id)
    if proposal.get("owner_scope") != require_owner(authorization)[1].scoped_id:
        raise ForbiddenError("WorkflowChangeProposal belongs to a different owner_scope")
    return proposal


class CreateProposalRequest(BaseModel):
    workflow_id: UUID
    base_draft_hash: str
    intent: str


class ApplyProposalRequest(BaseModel):
    base_draft_hash: str
    validated_plan_hash: str
    idempotency_key: str


class FixtureProposalRequest(BaseModel):
    workflow_id: UUID


@router.post("/test-fixtures/proposals", status_code=201)
async def create_test_fixture_proposal(body: FixtureProposalRequest, authorization: str | None = Header(None)) -> dict:
    """Debug-only server fixture; it never accepts browser graph operations."""
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    owner = require_owner(authorization)[1]
    workflow = SqlWorkflowService().get_workflow(body.workflow_id)
    if workflow.owner_scope.scoped_id != owner.scoped_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    draft = SqlWorkflowService().get_draft(body.workflow_id)
    return _architect.create(
        workflow_id=body.workflow_id, owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash, intent="test fixture: add brief",
        operations=[{"op": "add_node", "node": {"id": f"architect-fixture-brief-{uuid4().hex[:8]}", "type": "brief"}}],
    )


@router.post("/proposals", status_code=201)
async def create_proposal(body: CreateProposalRequest, authorization: str | None = Header(None)) -> dict:
    try:
        return _architect.generate_from_intent(owner_scope=require_owner(authorization)[1].scoped_id, **body.model_dump())
    except (ConflictError, NotFoundError, PolicyBlockedError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        return _owned_proposal(proposal_id, authorization)
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/proposals/{proposal_id}/diff")
async def proposal_diff(proposal_id: UUID, authorization: str | None = Header(None)) -> dict:
    try:
        _owned_proposal(proposal_id, authorization)
        return _architect.diff(proposal_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/proposals/{proposal_id}/apply")
async def apply_proposal(proposal_id: UUID, body: ApplyProposalRequest, authorization: str | None = Header(None)) -> dict:
    try:
        return _architect.apply(proposal_id=proposal_id, owner_scope=require_owner(authorization)[1].scoped_id, **body.model_dump())
    except (ConflictError, NotFoundError, ValidationError_, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
