"""Project API Routes — production wiring uses PostgreSQL.

Persistent Project CRUD with owner-scope enforcement.  In-memory
``ProjectService`` / ``ResourceLibrary`` remain as unit-test doubles and
are **not** wired into the API surface.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError
from src.infra.db.identity_repository import SqlIdentityRepository, get_session_store
from src.infra.db.project_repository import SqlProjectRepository
from src.schemas.models import OwnerScope

router = APIRouter(prefix="/api/v1/projects", tags=["project"])

# ---------------------------------------------------------------------------
# Singleton services — durable PostgreSQL-backed
# ---------------------------------------------------------------------------

_identity = SqlIdentityRepository()
_sessions = get_session_store()
_projects = SqlProjectRepository()


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> OwnerScope:
    """Validate token and return the OwnerScope for the bearer."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if parts[0].lower() != "bearer" or len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = parts[1]
    try:
        account_id = _sessions.account_for_token(token)
    except (NotFoundError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return OwnerScope(kind="user", id=account_id)


def _project_to_response(p: Any) -> ProjectResponse:
    return ProjectResponse(
        project_id=str(p.project_id),
        owner_scope=p.owner_scope,
        name=p.name,
        description=p.description,
        status=p.status.value if hasattr(p.status, "value") else str(p.status),
        default_entry=p.default_entry,
        created_at=p.created_at.isoformat() if hasattr(p.created_at, "isoformat") else str(p.created_at),
        updated_at=p.updated_at.isoformat() if hasattr(p.updated_at, "isoformat") else str(p.updated_at),
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid project id") from exc


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(body: CreateProjectRequest, authorization: str | None = Header(None)):
    """Create a new project."""
    owner = _resolve_owner(authorization)
    project = _projects.create_project(
        owner_scope=owner,
        name=body.name,
        description=body.description,
    )
    return _project_to_response(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    authorization: str | None = Header(None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List all projects for the authenticated user."""
    owner = _resolve_owner(authorization)
    rows = _projects.list_projects(owner, offset=offset, limit=limit)
    return [_project_to_response(p) for p in rows]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, authorization: str | None = Header(None)):
    """Get a single project by ID (owner-validated)."""
    owner = _resolve_owner(authorization)
    try:
        project = _projects.get_project(_uuid(project_id), owner)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _project_to_response(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    authorization: str | None = Header(None),
):
    """Update project name/description."""
    owner = _resolve_owner(authorization)
    try:
        _projects.get_project(_uuid(project_id), owner)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    from src.infra.db.session import get_session_factory
    with get_session_factory().begin() as session:
        from src.infra.db.models import ProjectModel
        row = session.get(ProjectModel, _uuid(project_id))
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        session.flush()
    return _project_to_response(row)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(project_id: str, authorization: str | None = Header(None)):
    owner = _resolve_owner(authorization)
    try:
        project = _projects.archive(_uuid(project_id), owner)
    except CrossOwnerError as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict())
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _project_to_response(project)


@router.post("/{project_id}/restore", response_model=ProjectResponse)
async def restore_project(project_id: str, authorization: str | None = Header(None)):
    owner = _resolve_owner(authorization)
    try:
        project = _projects.restore(_uuid(project_id), owner)
    except CrossOwnerError as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict())
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _project_to_response(project)


@router.delete("/{project_id}", status_code=202)
async def delete_project(project_id: str, authorization: str | None = Header(None)):
    owner = _resolve_owner(authorization)
    try:
        _projects.mark_deletion_pending(_uuid(project_id), owner)
    except CrossOwnerError as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict())
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return {"status": "deletion_pending"}