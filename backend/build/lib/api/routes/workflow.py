"""
ToonFlow Backend — API Routes for Workflow
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from src.domain.workflow.compiler import WorkflowCompiler
from src.schemas.models import CompiledExecutionPlan, RegistrySnapshot

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


@router.get("/")
async def list_workflows():
    """List workflows for current project."""
    return {"workflows": []}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: uuid.UUID):
    """Get a specific workflow."""
    return {"workflow_id": str(workflow_id)}


@router.post("/{workflow_id}/compile")
async def compile_workflow(workflow_id: uuid.UUID):
    """Compile a workflow revision into an execution plan."""
    compiler = WorkflowCompiler()
    registry = RegistrySnapshot(snapshot_id=uuid.uuid4())
    # In real implementation, load the workflow graph from DB
    return {"message": "compile endpoint ready"}
