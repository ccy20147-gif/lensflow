"""TF-WF-005: Artifact API Routes — PostgreSQL-backed.

Persistent ArtifactVersion CRUD with cross-owner enforcement.  The
in-memory ``ArtifactService`` / ``ResourceService`` remain as unit-test
doubles and are **not** wired into the API surface.

Resource endpoints are still served by the in-memory service until the
Foundation ``ResourceModel`` / ``ResourceRevisionModel`` ORM tables
land in a follow-up migration.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    NotFoundError,
    ValidationError_,
)
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.identity_repository import get_session_store
from src.infra.db.resource_repository import SqlResourceRepository
from src.schemas.models import (
    ArtifactRef,
    ArtifactVersion,
    OwnerScope,
)

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])

# Singleton durable service.
_artifact = SqlArtifactRepository()
_resources = SqlResourceRepository()
_sessions = get_session_store()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> OwnerScope:
    if not authorization or len(authorization.split()) != 2 or authorization.split()[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    try:
        return OwnerScope(kind="user", id=_sessions.account_for_token(authorization.split()[1]))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


class ArtifactVersionRequest(BaseModel):
    artifact_id: UUID | None = None
    schema_id: str
    schema_version: int = 1
    content_uri: str | None = None
    content_json: dict[str, Any] | None = None
    created_by_run_id: UUID | None = None
    lineage_input_refs: list[dict[str, Any]] | None = None
    # Storage placement is immutable provenance.  Clients may declare a
    # durable blob locator at creation, but can never patch it afterwards.
    blob_uri: str | None = None
    metadata: dict[str, Any] | None = None
    content_hash: str = ""


class CreateResourceRequest(BaseModel):
    resource_type: str
    content_artifact_version_id: UUID


class ElevateOcRequest(BaseModel):
    """Promote an OC embedded in an immutable World Revision into a Resource."""
    content_artifact_version_id: UUID
    source_world_revision_id: UUID
    source_local_id: str
    elevation_event_id: UUID | None = None


class SaveResourceDraftRequest(BaseModel):
    content_artifact_version_id: UUID
    base_draft_version: int


class FreezeResourceRequest(BaseModel):
    base_draft_version: int


class GrantResourceRequest(BaseModel):
    grantee_account_id: UUID
    capability_actions: list[str]


def _row_to_schema(row: Any) -> ArtifactVersion:
    """Convert an ORM row into the public ArtifactVersion schema."""
    kind, _, owner_id = row.owner_scope.partition(":")
    return ArtifactVersion.model_validate(
        {
            "artifact_id": row.artifact_id,
            "artifact_version_id": row.artifact_version_id,
            "schema_id": row.schema_id,
            "schema_version": row.schema_version,
            "owner_scope": {"kind": kind, "id": owner_id},
            "content_uri": row.content_uri or "",
            "content_json": row.content_json or {},
            "lineage_input_refs": row.lineage_input_refs or [],
            "created_by_run_id": row.created_by_run_id,
            "content_hash": row.content_hash or "",
        }
    )


# ------------------------------------------------------------------
# ArtifactVersion endpoints
# ------------------------------------------------------------------


@router.post("/versions", response_model=ArtifactVersion)
async def create_artifact_version(
    body: ArtifactVersionRequest, authorization: str | None = Header(None),
) -> ArtifactVersion:
    """Create a new immutable ArtifactVersion."""
    owner_scope = _resolve_owner(authorization)
    try:
        row = _artifact.create_version(
            owner_scope=owner_scope,
            schema_id=body.schema_id,
            schema_version=body.schema_version,
            content_uri=body.content_uri or "",
            content_json=body.content_json,
            content_hash=body.content_hash,
            created_by_run_id=body.created_by_run_id,
            lineage_input_refs=body.lineage_input_refs,
            metadata=body.metadata,
            blob_uri=body.blob_uri,
            artifact_id=body.artifact_id,
        )
    except ValidationError_ as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _row_to_schema(row)


@router.get("/versions/{version_id}", response_model=ArtifactVersion)
async def get_artifact_version(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> ArtifactVersion:
    """Get an artifact version by ID (with optional owner scope check)."""
    owner_scope = _resolve_owner(authorization)
    try:
        row = _artifact.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _row_to_schema(row)


@router.get("/versions", response_model=list[ArtifactVersion])
async def list_artifact_versions(
    schema_id: str | None = None,
    artifact_id: UUID | None = None,
    authorization: str | None = Header(None),
    offset: int = 0,
    limit: int = 50,
) -> list[ArtifactVersion]:
    owner_scope = _resolve_owner(authorization)
    rows = _artifact.list_versions(
        owner_scope=owner_scope,
        schema_id=schema_id,
        artifact_id=artifact_id,
        offset=offset,
        limit=limit,
    )
    return [_row_to_schema(r) for r in rows]


@router.get("/versions/{version_id}/lineage")
async def get_lineage(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner_scope = _resolve_owner(authorization)
    try:
        _artifact.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _artifact.lineage(version_id)


@router.get("/versions/{version_id}/stale-downstream")
async def find_stale_downstream(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> list[str]:
    """Return version_ids that depend on the upstream ArtifactVersion."""
    owner_scope = _resolve_owner(authorization)
    try:
        _artifact.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    upstream_artifact_id = _artifact.get_version(version_id, owner_scope).artifact_id
    return [str(v) for v in _artifact.stale_downstream(upstream_artifact_id, owner_scope)]


# ------------------------------------------------------------------
# ArtifactRef resolution (cross-owner check)
# ------------------------------------------------------------------


@router.post("/resolve-ref", response_model=ArtifactRef)
async def resolve_artifact_ref(
    artifact_id: UUID,
    artifact_version_id: UUID,
    authorization: str | None = Header(None),
) -> ArtifactRef:
    """Resolve an ArtifactRef with cross-owner boundary check."""
    owner = _resolve_owner(authorization)
    try:
        row = _artifact.get_version(artifact_version_id, owner)
    except CrossOwnerError as exc:
        raise HTTPException(status_code=403, detail=exc.to_dict())
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return ArtifactRef(
        artifact_id=row.artifact_id,
        artifact_version_id=row.artifact_version_id,
        schema_id=row.schema_id,
        schema_version=row.schema_version,
    )


@router.post("/resources", status_code=201)
async def create_resource(body: CreateResourceRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        row = _resources.create(owner, body.resource_type, body.content_artifact_version_id)
        draft = _resources.get_draft(row.resource_id, owner)
        return {"resource_id": str(row.resource_id), "resource_type": row.resource_type, "draft_version": draft.draft_version}
    except (NotFoundError, CrossOwnerError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/resources/elevate-oc", status_code=201)
async def elevate_world_oc(body: ElevateOcRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a Character/OC Resource with an immutable World-local origin."""
    owner = _resolve_owner(authorization)
    try:
        row = _resources.create(
            owner,
            "character",
            body.content_artifact_version_id,
            source_world_revision_id=body.source_world_revision_id,
            source_local_id=body.source_local_id,
            elevation_event_id=body.elevation_event_id,
        )
        draft = _resources.get_draft(row.resource_id, owner)
        return {**row.model_dump(mode="json"), "draft_version": draft.draft_version}
    except (NotFoundError, CrossOwnerError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.get("/resources")
async def list_resources(resource_type: str | None = None, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    """List resource identities plus their current editable Draft metadata.

    A ResourceRevision is never resolved as an implicit latest input here;
    callers that execute a workflow must still select a fixed revision.
    """
    owner = _resolve_owner(authorization)
    rows: list[dict[str, Any]] = []
    for resource in _resources.list_resources(owner, resource_type):
        draft = _resources.get_draft(resource.resource_id, owner)
        revisions = _resources.list_revisions(resource.resource_id, owner)
        rows.append({
            **resource.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "active_revision_id": next((str(revision.revision_id) for revision in revisions if revision.revision_status.value == "active"), None),
            "revision_count": len(revisions),
        })
    return rows


@router.get("/resources/{resource_id}/revisions")
async def list_resource_revisions(resource_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    owner = _resolve_owner(authorization)
    try:
        return [revision.model_dump(mode="json") for revision in _resources.list_revisions(resource_id, owner)]
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.get("/resources/{resource_id}/provenance")
async def resource_provenance(resource_id: UUID, authorization: str | None = Header(None)) -> dict[str, object]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.provenance(resource_id, owner)
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/resources/rebuild-projection")
async def rebuild_resource_projection(authorization: str | None = Header(None)) -> dict[str, object]:
    """Operational proof that library state is recoverable from canonical rows."""
    owner = _resolve_owner(authorization)
    return _resources.rebuild_projection(owner)


@router.get("/resources/{resource_id}/draft")
async def get_resource_draft(resource_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.get_draft(resource_id, owner).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.put("/resources/{resource_id}/draft")
async def save_resource_draft(resource_id: UUID, body: SaveResourceDraftRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.save_draft(resource_id, owner, body.content_artifact_version_id, body.base_draft_version).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except Exception as exc:
        if getattr(exc, "status_code", None) == 409:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


@router.post("/resources/{resource_id}/revisions", status_code=201)
async def freeze_resource(resource_id: UUID, body: FreezeResourceRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.freeze(resource_id, owner, body.base_draft_version).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/resources/{resource_id}/revisions/{revision_id}/grants", status_code=201)
async def grant_resource(resource_id: UUID, revision_id: UUID, body: GrantResourceRequest, authorization: str | None = Header(None)) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        # Do not let a valid revision be granted through an unrelated resource
        # URL.  Apart from preventing confusing audit trails this makes the
        # path itself an authorization boundary.
        _resources.get(resource_id, owner)
        _resources.resolve_ref(resource_id, revision_id, owner, None)
        grant_id = _resources.grant(revision_id, owner, OwnerScope(kind="user", id=body.grantee_account_id), capability_actions=body.capability_actions)
        return {"grant_snapshot_id": str(grant_id)}
    except (NotFoundError, CrossOwnerError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.delete("/resources/{resource_id}/revisions/{revision_id}/grants/{grant_snapshot_id}")
async def revoke_resource_grant(resource_id: UUID, revision_id: UUID, grant_snapshot_id: UUID,
                                authorization: str | None = Header(None)) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        _resources.get(resource_id, owner)
        # Bind the URL's resource identity to the target revision before the
        # grant mutation; owning two resources must not permit a confused
        # deputy revoke through an unrelated path.
        _resources.resolve_ref(resource_id, revision_id, owner, None)
        _resources.revoke_grant(revision_id, grant_snapshot_id, owner)
        return {"status": "revoked", "grant_snapshot_id": str(grant_snapshot_id)}
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/resources/{resource_id}/revisions/{revision_id}/resolve-ref")
async def resolve_resource_ref(resource_id: UUID, revision_id: UUID, grant_snapshot_id: UUID | None = None, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.resolve_ref(resource_id, revision_id, owner, grant_snapshot_id).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
