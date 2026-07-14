"""TF-WF-005: Artifact & Resource API Routes

Endpoints for ArtifactVersion CRUD, Resource/Draft/Revision management,
lineage queries, and cross-owner boundary enforcement.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    NotFoundError,
    ValidationError_,
)
from src.schemas.models import (
    ArtifactRef,
    ArtifactVersion,
    OwnerScope,
    Resource,
    ResourceDraft,
    ResourceRef,
    ResourceRevision,
)

from src.domain.artifact.artifact_service import ArtifactService
from src.domain.artifact.resource_service import ResourceService

_artifact_svc = ArtifactService()
_resource_svc = ResourceService(artifact_service=_artifact_svc)

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])

# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


def _parse_owner(
    owner_kind: str | None = None, owner_id: UUID | None = None
) -> OwnerScope | None:
    if owner_id and owner_kind:
        return OwnerScope(kind=owner_kind, id=owner_id)
    return None


# ------------------------------------------------------------------
# ArtifactVersion endpoints
# ------------------------------------------------------------------


@router.post("/versions", response_model=ArtifactVersion)
async def create_artifact_version(
    artifact_id: UUID | None = None,
    schema_id: str = "",
    schema_version: int = 1,
    owner_kind: str | None = None,
    owner_id: UUID | None = None,
    content_uri: str | None = None,
    content_json: dict[str, Any] | None = None,
    created_by_run_id: UUID | None = None,
) -> ArtifactVersion:
    """Create a new immutable ArtifactVersion."""
    owner_scope = _parse_owner(owner_kind, owner_id)
    try:
        return _artifact_svc.create_artifact_version(
            artifact_id=artifact_id,
            schema_id=schema_id,
            schema_version=schema_version,
            owner_scope=owner_scope,
            content_uri=content_uri,
            content_json=content_json,
            created_by_run_id=created_by_run_id,
        )
    except (ValueError, CrossOwnerError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/versions/{version_id}", response_model=ArtifactVersion)
async def get_artifact_version(version_id: UUID) -> ArtifactVersion:
    """Get an artifact version by ID."""
    try:
        return _artifact_svc.get_artifact_version(version_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/versions", response_model=list[ArtifactVersion])
async def list_artifact_versions(
    artifact_id: UUID | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[ArtifactVersion]:
    """List artifact versions, optionally filtered by artifact_id."""
    return _artifact_svc.list_artifact_versions(
        artifact_id=artifact_id, offset=offset, limit=limit
    )


@router.get("/versions/{version_id}/lineage")
async def get_lineage(version_id: UUID) -> dict[str, Any]:
    """Get the full lineage for an artifact version."""
    try:
        return _artifact_svc.get_lineage(version_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/versions/{version_id}/stale-downstream")
async def find_stale_downstream(version_id: UUID) -> list[ArtifactVersion]:
    """Find artifact versions that directly consume the given version as input."""
    try:
        return _artifact_svc.find_stale_downstream(version_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# ArtifactRef resolution (with cross-owner check)
# ------------------------------------------------------------------


@router.post("/resolve-ref", response_model=ArtifactRef)
async def resolve_artifact_ref(
    artifact_id: UUID,
    artifact_version_id: UUID,
    owner_kind: str,
    owner_id: UUID,
) -> ArtifactRef:
    """Resolve an ArtifactRef with cross-owner boundary check."""
    owner = OwnerScope(kind=owner_kind, id=owner_id)
    try:
        return _artifact_svc.get_artifact_ref(artifact_id, artifact_version_id, owner)
    except (NotFoundError, CrossOwnerError) as e:
        if isinstance(e, NotFoundError):
            raise HTTPException(status_code=404, detail=e.to_dict())
        raise HTTPException(status_code=403, detail=e.to_dict())


# ------------------------------------------------------------------
# Resource endpoints
# ------------------------------------------------------------------


@router.post("/resources", response_model=Resource)
async def create_resource(
    resource_id: UUID | None = None,
    resource_type: str = "generic",
    owner_kind: str = "user",
    owner_id: UUID = UUID(int=0),
) -> Resource:
    """Create a new Resource."""
    owner_scope = OwnerScope(kind=owner_kind, id=owner_id)
    try:
        return _resource_svc.create_resource(
            resource_id=resource_id,
            resource_type=resource_type,
            owner_scope=owner_scope,
        )
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/resources", response_model=list[Resource])
async def list_resources(
    owner_kind: str | None = None,
    owner_id: UUID | None = None,
    resource_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[Resource]:
    """List resources with optional filters."""
    owner_scope = _parse_owner(owner_kind, owner_id)
    return _resource_svc.list_resources(
        owner_scope=owner_scope,
        resource_type=resource_type,
        offset=offset,
        limit=limit,
    )


@router.get("/resources/{resource_id}", response_model=Resource)
async def get_resource(resource_id: UUID) -> Resource:
    """Get a resource by ID."""
    try:
        return _resource_svc.get_resource(resource_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: UUID) -> dict[str, str]:
    """Delete a resource."""
    try:
        _resource_svc.delete_resource(resource_id)
        return {"status": "deleted"}
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# Resource Draft endpoints
# ------------------------------------------------------------------


@router.get("/resources/{resource_id}/draft", response_model=ResourceDraft)
async def get_resource_draft(resource_id: UUID) -> ResourceDraft:
    """Get the current draft for a resource."""
    try:
        return _resource_svc.get_draft(resource_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.put("/resources/{resource_id}/draft", response_model=ResourceDraft)
async def save_resource_draft(
    resource_id: UUID,
    content_artifact_version_id: UUID,
    base_draft_version: int,
) -> ResourceDraft:
    """Save resource draft with CAS on draft_version."""
    try:
        return _resource_svc.save_draft(
            resource_id=resource_id,
            content_artifact_version_id=content_artifact_version_id,
            base_draft_version=base_draft_version,
        )
    except ConflictError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# Resource Revision endpoints
# ------------------------------------------------------------------


@router.post(
    "/resources/{resource_id}/revisions", response_model=ResourceRevision
)
async def freeze_revision(
    resource_id: UUID, base_draft_version: int
) -> ResourceRevision:
    """Freeze the current draft into an immutable ResourceRevision (CAS)."""
    try:
        return _resource_svc.freeze_revision(resource_id, base_draft_version)
    except ConflictError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get(
    "/resources/{resource_id}/revisions", response_model=list[ResourceRevision]
)
async def list_resource_revisions(
    resource_id: UUID, offset: int = 0, limit: int = 50
) -> list[ResourceRevision]:
    """List all revisions for a resource."""
    return _resource_svc.list_revisions(resource_id, offset=offset, limit=limit)


@router.get(
    "/revisions/{revision_id}", response_model=ResourceRevision
)
async def get_resource_revision(revision_id: UUID) -> ResourceRevision:
    """Get a specific resource revision."""
    try:
        return _resource_svc.get_revision(revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/revisions/{revision_id}/retire", response_model=ResourceRevision
)
async def retire_resource_revision(revision_id: UUID) -> ResourceRevision:
    """Retire a resource revision."""
    try:
        return _resource_svc.retire_revision(revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# ResourceRef resolution (cross-owner)
# ------------------------------------------------------------------


@router.post("/resolve-resource-ref", response_model=ResourceRef)
async def resolve_resource_ref(
    resource_id: UUID,
    revision_id: UUID,
    owner_kind: str,
    owner_id: UUID,
    grant_snapshot_id: UUID | None = None,
) -> ResourceRef:
    """Resolve a ResourceRef with cross-owner boundary check.

    Same-owner: grant_snapshot_id is optional.
    Cross-owner: grant_snapshot_id is required.
    """
    requesting_scope = OwnerScope(kind=owner_kind, id=owner_id)
    try:
        return _resource_svc.resolve_resource_ref(
            resource_id=resource_id,
            revision_id=revision_id,
            requesting_scope=requesting_scope,
            grant_snapshot_id=grant_snapshot_id,
        )
    except (NotFoundError, CrossOwnerError) as e:
        if isinstance(e, CrossOwnerError):
            raise HTTPException(status_code=403, detail=e.to_dict())
        raise HTTPException(status_code=404, detail=e.to_dict())
