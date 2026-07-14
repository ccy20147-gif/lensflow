"""Project API Routes — project CRUD, archive/restore, resource library."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.core.exceptions import CrossOwnerError
from src.domain.identity.identity_service import IdentityService
from src.domain.identity.session_service import SessionService
from src.domain.project.project_service import ProjectService
from src.domain.project.resource_library import ResourceLibrary
from src.schemas.models import OwnerScope

router = APIRouter(prefix="/api/v1/projects", tags=["project"])

# ---------------------------------------------------------------------------
# Singleton services
# ---------------------------------------------------------------------------

_session_service = SessionService()
_identity_service = IdentityService(session_service=_session_service)
_project_service = ProjectService()
_resource_library = ResourceLibrary()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    project_id: str
    owner_scope: str
    name: str
    description: str
    status: str
    default_entry: str
    created_at: str
    updated_at: str


class ResourceResponse(BaseModel):
    resource_id: str
    resource_type: str
    owner_scope: str
    created_at: str


class PaginatedResources(BaseModel):
    items: list[ResourceResponse]
    total: int
    offset: int
    limit: int


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> tuple[Any, OwnerScope]:
    """Validate token and return (account, owner_scope)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if parts[0].lower() != "bearer" or len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = parts[1]
    account, _ = _identity_service.validate_token(token)
    owner_scope = _identity_service._to_owner_scope(str(account.account_id))
    return account, owner_scope


def _project_to_response(p: Any) -> ProjectResponse:
    return ProjectResponse(
        project_id=str(p.project_id),
        owner_scope=p.owner_scope.scoped_id,
        name=p.name,
        description=p.description,
        status=p.status.value if hasattr(p.status, "value") else str(p.status),
        default_entry=p.default_entry,
        created_at=p.created_at.isoformat() if hasattr(p.created_at, "isoformat") else str(p.created_at),
        updated_at=p.updated_at.isoformat() if hasattr(p.updated_at, "isoformat") else str(p.updated_at),
    )


def _resource_to_response(r: Any) -> ResourceResponse:
    return ResourceResponse(
        resource_id=str(r.resource_id),
        resource_type=r.resource_type,
        owner_scope=r.owner_scope.scoped_id,
        created_at=r.created_at.isoformat() if hasattr(r.created_at, "isoformat") else str(r.created_at),
    )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest, authorization: str | None = Header(None)):
    """Create a new project."""
    _, owner = _resolve_owner(authorization)
    project = _project_service.create_project(
        owner_scope=owner,
        name=body.name,
        description=body.description,
    )
    return _project_to_response(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(authorization: str | None = Header(None)):
    """List all projects for the authenticated user."""
    _, owner = _resolve_owner(authorization)
    projects = _project_service.list_projects(owner)
    return [_project_to_response(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, authorization: str | None = Header(None)):
    """Get a single project by ID (owner-validated)."""
    _, owner = _resolve_owner(authorization)
    try:
        project = _project_service.get_project(project_id, owner)
        return _project_to_response(project)
    except CrossOwnerError:
        # Never leak project existence (AC-5)
        raise HTTPException(status_code=404, detail="Project not found")


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, body: UpdateProjectRequest, authorization: str | None = Header(None)):
    """Update project metadata."""
    _, owner = _resolve_owner(authorization)
    try:
        project = _project_service.update_project(
            project_id=project_id,
            caller_owner=owner,
            name=body.name,
            description=body.description,
        )
        return _project_to_response(project)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------------------------------------------------------------------------
# Archive / Restore
# ---------------------------------------------------------------------------


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(project_id: str, authorization: str | None = Header(None)):
    """Archive a project (read-only)."""
    _, owner = _resolve_owner(authorization)
    try:
        project = _project_service.archive_project(project_id, owner)
        return _project_to_response(project)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/{project_id}/restore", response_model=ProjectResponse)
async def restore_project(project_id: str, authorization: str | None = Header(None)):
    """Restore an archived project."""
    _, owner = _resolve_owner(authorization)
    try:
        project = _project_service.restore_project(project_id, owner)
        return _project_to_response(project)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------------------------------------------------------------------------
# Deletion flow
# ---------------------------------------------------------------------------


@router.post("/{project_id}/deletion-request", response_model=ProjectResponse)
async def request_deletion(project_id: str, authorization: str | None = Header(None)):
    """Initiate project deletion."""
    _, owner = _resolve_owner(authorization)
    try:
        project = _project_service.request_deletion(project_id, owner)
        return _project_to_response(project)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/{project_id}/deletion-confirm", response_model=StatusResponse)
async def confirm_deletion(project_id: str, authorization: str | None = Header(None)):
    """Confirm and finalize project deletion."""
    _, owner = _resolve_owner(authorization)
    try:
        _project_service.confirm_deletion(project_id, owner)
        return StatusResponse(status="deleted")
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------------------------------------------------------------------------
# Workflow associations
# ---------------------------------------------------------------------------


@router.get("/{project_id}/workflows", response_model=list[str])
async def list_project_workflows(project_id: str, authorization: str | None = Header(None)):
    """List workflow IDs in a project."""
    _, owner = _resolve_owner(authorization)
    try:
        return _project_service.list_workflows(project_id, owner)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")


# ---------------------------------------------------------------------------
# Resource Library (FR-4)
# ---------------------------------------------------------------------------


@router.get("/{project_id}/resources", response_model=PaginatedResources)
async def list_resources(
    project_id: str,
    resource_type: str | None = Query(None),
    name: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    authorization: str | None = Header(None),
):
    """List resources in a project with filtering."""
    _, owner = _resolve_owner(authorization)
    try:
        _project_service.get_project(project_id, owner)
    except CrossOwnerError:
        raise HTTPException(status_code=404, detail="Project not found")

    resources, total = _resource_library.list_resources(
        owner_scope=owner,
        resource_type=resource_type,
        name_query=name,
        limit=limit,
        offset=offset,
    )
    return PaginatedResources(
        items=[_resource_to_response(r) for r in resources],
        total=total,
        offset=offset,
        limit=limit,
    )
