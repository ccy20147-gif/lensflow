"""TF-WF-005 + TF-OPS-003 Artifact / Blob / Resource API routes.

PG-backed endpoints that close the Batch A contracts:

* ``/api/v1/blobs`` — upload session lifecycle, complete, read,
  delete-check, migrate, lifecycle actions.
* ``/api/v1/artifacts/versions`` — immutable ArtifactVersion creation
  with strict Blob-availability and lineage-rollback invariants.
* ``/api/v1/artifacts/resolve-ref`` — same-owner only; cross-owner
  ``ArtifactRef`` resolution returns 403 even when the caller passes a
  Blob URL or grant evidence.
* ``/api/v1/resources/*`` — ResourceDraft / ResourceRevision CAS with
  structured ``CasConflict`` payload, OutputBinding/SelectionRecord
  promotion gate, cross-owner ``EntitlementDecision`` recompute, and
  projection rebuild.
"""
from __future__ import annotations

import io
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    NotFoundError,
    SafeError,
    ValidationError_,
)
from src.infra.blob.blob_service import SqlBlobRepository, sha256_hex
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.identity_repository import get_session_store
from src.infra.db.resource_repository import SqlResourceRepository
from src.schemas.models import (
    ArtifactRef,
    ArtifactVersion,
    EntitlementDecision,
    OwnerScope,
    PromotionSource,
    ResourceRef,
    ResourceRevision,
)


router = APIRouter(prefix="/api/v1", tags=["artifacts"])

# Singleton durable services.
_artifacts = SqlArtifactRepository()
_resources = SqlResourceRepository()
_blobs = SqlBlobRepository()
_sessions = get_session_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> OwnerScope:
    if not authorization or len(authorization.split()) != 2 or authorization.split()[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    try:
        return OwnerScope(kind="user", id=_sessions.account_for_token(authorization.split()[1]))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def _safe_error_response(exc: SafeError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _row_to_artifact(row: Any) -> ArtifactVersion:
    kind, _, owner_id = str(row.owner_scope).partition(":")
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
    )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Pydantic request shapes
# ---------------------------------------------------------------------------


class ArtifactVersionRequest(BaseModel):
    artifact_id: UUID | None = None
    schema_id: str
    schema_version: int = 1
    content_uri: str | None = None
    content_json: dict[str, Any] | None = None
    created_by_run_id: UUID | None = None
    lineage_input_refs: list[dict[str, Any]] | None = None
    blob_uri: str | None = None
    metadata: dict[str, Any] | None = None
    content_hash: str = ""
    blob_id: UUID | None = None


class BlobMutationRequest(BaseModel):
    blob_id: UUID | None = None
    blob_uri: str | None = None


class BlobStartUploadRequest(BaseModel):
    expected_size_bytes: int
    expected_content_hash: str
    media_type: str = "application/octet-stream"
    idempotency_key: str
    part_state: list[dict[str, Any]] | None = None
    expires_at: str | None = None


class BlobCompleteRequest(BaseModel):
    session_id: UUID
    content_base64: str | None = None
    declared_size: int | None = None
    declared_hash: str | None = None
    durability_receipt: dict[str, Any] | None = None


class BlobAbortRequest(BaseModel):
    session_id: UUID


class CreateResourceRequest(BaseModel):
    """Bootstrap resource creation.  The server-side create() will
    refuse the call when the supplied ArtifactVersion is already
    cited by a same-owner OutputBinding / SelectionRecord, so the
    contract "run outputs go through /resources/promote" is enforced
    by the repository rather than the request shape.
    """

    resource_type: str
    content_artifact_version_id: UUID
    source_world_revision_id: UUID | None = None
    source_local_id: str | None = None
    elevation_event_id: UUID | None = None


class ElevateOcRequest(BaseModel):
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


class PromotionRequest(BaseModel):
    """TF-PLT-003 promotion gate input.

    Either an OutputBinding (single candidate) or a SelectionRecord
    (one of several selected_refs).  Bare ``artifact_id`` is forbidden.
    """

    resource_type: str
    source: PromotionSource


class SupersedePromotionSourceRequest(BaseModel):
    ref_kind: str
    ref_id: UUID
    superseded_by_ref_id: UUID | None = None
    reason: str = ""


class EvaluateEntitlementRequest(BaseModel):
    action: str
    grant_snapshot_id: UUID | None = None


# ===========================================================================
# ArtifactVersion endpoints
# ===========================================================================


@router.post("/artifacts/versions", response_model=ArtifactVersion)
async def create_artifact_version(
    body: ArtifactVersionRequest,
    authorization: str | None = Header(None),
) -> ArtifactVersion:
    owner_scope = _resolve_owner(authorization)
    try:
        row = _artifacts.create_version(
            owner_scope=owner_scope,
            schema_id=body.schema_id,
            schema_version=body.schema_version,
            content_uri=body.content_uri or "",
            content_json=body.content_json,
            content_hash=body.content_hash,
            created_by_run_id=body.created_by_run_id,
            lineage_input_refs=body.lineage_input_refs,
            metadata=body.metadata,
            blob_id=body.blob_id,
            blob_uri=body.blob_uri,
            artifact_id=body.artifact_id,
        )
    except (ValidationError_, ConflictError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)
    return _row_to_artifact(row)


@router.get("/artifacts/versions/{version_id}", response_model=ArtifactVersion)
async def get_artifact_version(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> ArtifactVersion:
    owner_scope = _resolve_owner(authorization)
    try:
        row = _artifacts.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)
    return _row_to_artifact(row)


@router.get("/artifacts/versions", response_model=list[ArtifactVersion])
async def list_artifact_versions(
    schema_id: str | None = None,
    artifact_id: UUID | None = None,
    authorization: str | None = Header(None),
    offset: int = 0,
    limit: int = 50,
) -> list[ArtifactVersion]:
    owner_scope = _resolve_owner(authorization)
    rows = _artifacts.list_versions(
        owner_scope=owner_scope,
        schema_id=schema_id,
        artifact_id=artifact_id,
        offset=offset,
        limit=limit,
    )
    return [_row_to_artifact(r) for r in rows]


@router.get("/artifacts/versions/{version_id}/lineage")
async def get_lineage(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner_scope = _resolve_owner(authorization)
    try:
        _artifacts.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)
    return _artifacts.lineage(version_id)


@router.get("/artifacts/versions/{version_id}/stale-downstream")
async def find_stale_downstream(
    version_id: UUID,
    authorization: str | None = Header(None),
) -> list[str]:
    owner_scope = _resolve_owner(authorization)
    try:
        row = _artifacts.get_version(version_id, owner_scope)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)
    return [str(v) for v in _artifacts.stale_downstream(row.artifact_id, owner_scope)]


@router.post("/artifacts/blobs/delete-check")
async def check_blob_delete(body: BlobMutationRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Compatibility wrapper.

    ``blob_id`` is the strict, canonical path: the server scans the
    ArtifactBlobRefModel join plus the durable projection.  ``blob_uri``
    is the legacy text-match path preserved for existing callers — the
    durable scan runs against ArtifactVersionModel.blob_uri and the
    assertion still rejects destructive ops while any canonical row
    references the URI.
    """
    owner = _resolve_owner(authorization)
    try:
        if body.blob_id is not None:
            refs = _blobs.references_for(body.blob_id, owner)
            if any(refs[k] for k in ("artifact_version", "resource_revision", "invocation_record")):
                raise ValidationError_(
                    "Blob 仍被有效 ArtifactVersion 引用，禁止破坏性操作",
                    details=refs,
                )
            return {"allowed": True, "blob_id": str(body.blob_id)}
        if body.blob_uri:
            _artifacts.assert_blob_mutation_allowed_for_uri(body.blob_uri, owner)
            return {"allowed": True, "blob_uri": body.blob_uri}
    except (ValidationError_, CrossOwnerError, NotFoundError) as exc:
        # The legacy callers expect a 409 to surface the conflict
        # explicitly; we honour that contract here.
        status = 409 if isinstance(exc, ValidationError_) and "仍被" in str(exc) else exc.status_code
        raise HTTPException(status_code=status, detail=exc.to_dict())
    raise HTTPException(status_code=422, detail={
        "error": {"code": "VALIDATION_ERROR", "message": "blob_id 或 blob_uri 必填其一"}
    })


# ---------------------------------------------------------------------------
# ArtifactRef resolution — same-owner only, even with Blob URL evidence
# ---------------------------------------------------------------------------


@router.post("/artifacts/resolve-ref", response_model=ArtifactRef)
async def resolve_artifact_ref(
    artifact_id: UUID,
    artifact_version_id: UUID,
    authorization: str | None = Header(None),
    grant_snapshot_id: UUID | None = None,
    blob_url: str | None = None,
) -> ArtifactRef:
    """Resolve an ArtifactRef with cross-owner boundary check.

    The endpoint refuses:

    * a cross-owner ``artifact_id`` (404 vs 403 — the version is just not
      visible to this owner),
    * any caller passing a Blob URL, signed-URL hint or grant evidence
      (ArtifactRef MUST NOT honour those — only the ResourceRef path
      carries cross-owner semantics).
    """
    owner = _resolve_owner(authorization)
    if blob_url or grant_snapshot_id:
        raise _safe_error_response(
            CrossOwnerError(),
        ) if grant_snapshot_id else HTTPException(
            status_code=422,
            detail={"error": {"code": "VALIDATION_ERROR", "message": "ArtifactRef 不能通过 Blob URL 解析"}},
        )
    try:
        row = _artifacts.get_version(artifact_version_id, owner)
    except CrossOwnerError as exc:
        raise _safe_error_response(exc)
    except NotFoundError as exc:
        raise _safe_error_response(exc)
    if row.artifact_id != artifact_id:
        raise _safe_error_response(NotFoundError("ArtifactVersion", str(artifact_version_id)))
    return ArtifactRef(
        artifact_id=row.artifact_id,
        artifact_version_id=row.artifact_version_id,
        schema_id=row.schema_id,
        schema_version=row.schema_version,
        owner_scope=owner,
    )


# ===========================================================================
# Resource endpoints
# ===========================================================================


@router.post("/artifacts/resources", status_code=201)
async def create_resource(body: CreateResourceRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Bootstrap resource creation.

    The server-side ``create()`` will refuse this call when the
    supplied ArtifactVersion is already cited by a same-owner
    OutputBinding / SelectionRecord — that bypass attempt is
    rejected as ``ConflictError`` (HTTP 409).  Run outputs MUST go
    through :meth:`/resources/promote`; World OC elevation through
    :meth:`/resources/elevate-oc`.
    """
    owner = _resolve_owner(authorization)
    try:
        row = _resources.create(
            owner,
            body.resource_type,
            body.content_artifact_version_id,
            source_world_revision_id=body.source_world_revision_id,
            source_local_id=body.source_local_id,
            elevation_event_id=body.elevation_event_id,
        )
        draft = _resources.get_draft(row.resource_id, owner)
        return {
            "resource_id": str(row.resource_id),
            "resource_type": row.resource_type,
            "draft_version": draft.draft_version,
            "promotion_source_kind": row.promotion_source_kind,
            "promotion_source_ref_id": str(row.promotion_source_ref_id) if row.promotion_source_ref_id else None,
            "promotion_source_artifact_version_id": str(row.promotion_source_artifact_version_id) if row.promotion_source_artifact_version_id else None,
        }
    except (NotFoundError, CrossOwnerError, ConflictError, ValidationError_) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/promote", status_code=201)
async def promote_via_output_binding_or_selection(
    body: PromotionRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Promote a Resource from a single valid OutputBinding/SelectionRecord.

    TF-PLT-003 / TF-WF-005 §16: bare ``artifact_id``, a superseded
    candidate, or a source that does not belong to the caller are all
    rejected.  The resolver runs in the same database transaction as
    the Resource insert; the immutable provenance
    (``promotion_source_kind`` / ``promotion_source_ref_id`` /
    ``promotion_source_artifact_version_id``) is persisted on the
    Resource row inside that transaction so a later ``create()`` cannot
    impersonate the promotion.
    """
    owner = _resolve_owner(authorization)
    try:
        resource, candidate_meta = _resources.promote_from_source(
            owner, body.source, body.resource_type,
        )
        draft = _resources.get_draft(resource.resource_id, owner)
        return {
            "resource_id": str(resource.resource_id),
            "resource_type": resource.resource_type,
            "draft_version": draft.draft_version,
            "promotion_source_kind": resource.promotion_source_kind,
            "promotion_source_ref_id": str(resource.promotion_source_ref_id) if resource.promotion_source_ref_id else None,
            "promotion_source_artifact_version_id": str(resource.promotion_source_artifact_version_id) if resource.promotion_source_artifact_version_id else None,
            "candidate": candidate_meta,
        }
    except (NotFoundError, CrossOwnerError, ConflictError, ValidationError_) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/supersede-promotion-source", status_code=201)
async def supersede_promotion_source(
    body: SupersedePromotionSourceRequest,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        supersede_id = _resources.supersede_promotion_source(
            owner,
            body.ref_kind,
            body.ref_id,
            superseded_by_ref_id=body.superseded_by_ref_id,
            reason=body.reason,
        )
        return {"supersede_id": str(supersede_id)}
    except (NotFoundError, CrossOwnerError, ConflictError, ValidationError_) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/elevate-oc", status_code=201)
async def elevate_world_oc(body: ElevateOcRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
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
        raise _safe_error_response(exc)


@router.get("/artifacts/resources")
async def list_resources(resource_type: str | None = None, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    owner = _resolve_owner(authorization)
    rows: list[dict[str, Any]] = []
    for resource in _resources.list_resources(owner, resource_type):
        draft = _resources.get_draft(resource.resource_id, owner)
        revisions = _resources.list_revisions(resource.resource_id, owner)
        rows.append({
            **resource.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "active_revision_id": next(
                (str(revision.revision_id) for revision in revisions if revision.revision_status.value == "active"),
                None,
            ),
            "revision_count": len(revisions),
        })
    return rows


@router.get("/artifacts/resources/{resource_id}/revisions")
async def list_resource_revisions(resource_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    owner = _resolve_owner(authorization)
    try:
        return [revision.model_dump(mode="json") for revision in _resources.list_revisions(resource_id, owner)]
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.get("/artifacts/resources/{resource_id}/provenance")
async def resource_provenance(resource_id: UUID, authorization: str | None = Header(None)) -> dict[str, object]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.provenance(resource_id, owner)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.get("/artifacts/resources/{resource_id}/revisions/{revision_id}/diff/{other_revision_id}")
async def diff_resource_revisions(
    resource_id: UUID,
    revision_id: UUID,
    other_revision_id: UUID,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        revisions = {item.revision_id: item for item in _resources.list_revisions(resource_id, owner)}
        left, right = revisions.get(revision_id), revisions.get(other_revision_id)
        if left is None or right is None:
            raise NotFoundError("ResourceRevision", str(revision_id if left is None else other_revision_id))
        left_artifact = _artifacts.get_version(left.content_artifact_version_id, owner)
        right_artifact = _artifacts.get_version(right.content_artifact_version_id, owner)
        left_content, right_content = left_artifact.content_json or {}, right_artifact.content_json or {}
        keys = sorted(set(left_content) | set(right_content)) if isinstance(left_content, dict) and isinstance(right_content, dict) else []
        return {
            "resource_id": str(resource_id),
            "from_revision_id": str(revision_id),
            "to_revision_id": str(other_revision_id),
            "from_artifact_version_id": str(left.content_artifact_version_id),
            "to_artifact_version_id": str(right.content_artifact_version_id),
            "changed_keys": [key for key in keys if left_content.get(key) != right_content.get(key)],
            "content_changed": left_content != right_content,
        }
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/rebuild-projection")
async def rebuild_resource_projection(authorization: str | None = Header(None)) -> dict[str, object]:
    owner = _resolve_owner(authorization)
    rebuilt_resources = _resources.rebuild_projection(owner)
    lineage_rows = _artifacts.rebuild_lineage_projection(owner)
    blob_refs = _blobs.rebuild_reference_index(owner)
    return {
        "source": "canonical_postgresql",
        "resources": rebuilt_resources["resources"],
        "lineage_projection_rows_rewritten": lineage_rows,
        "blob_reference_index_rows_written": blob_refs,
    }


@router.get("/artifacts/resources/{resource_id}/draft")
async def get_resource_draft(resource_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.get_draft(resource_id, owner).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.put("/artifacts/resources/{resource_id}/draft")
async def save_resource_draft(
    resource_id: UUID,
    body: SaveResourceDraftRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.save_draft(
            resource_id, owner, body.content_artifact_version_id, body.base_draft_version,
        ).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError, ValidationError_) as exc:
        raise _safe_error_response(exc)
    except ConflictError as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/{resource_id}/revisions", status_code=201)
async def freeze_resource(
    resource_id: UUID,
    body: FreezeResourceRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _resources.freeze(resource_id, owner, body.base_draft_version).model_dump(mode="json")
    except (NotFoundError, CrossOwnerError, ValidationError_) as exc:
        raise _safe_error_response(exc)
    except ConflictError as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/{resource_id}/revisions/{revision_id}/grants", status_code=201)
async def grant_resource(
    resource_id: UUID,
    revision_id: UUID,
    body: GrantResourceRequest,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        _resources.get(resource_id, owner)
        _resources.resolve_ref(resource_id, revision_id, owner, None)
        grant_id = _resources.grant(
            revision_id,
            owner,
            OwnerScope(kind="user", id=body.grantee_account_id),
            capability_actions=body.capability_actions,
        )
        return {"grant_snapshot_id": str(grant_id)}
    except (NotFoundError, CrossOwnerError, ValidationError_) as exc:
        raise _safe_error_response(exc)


@router.delete("/artifacts/resources/{resource_id}/revisions/{revision_id}/grants/{grant_snapshot_id}")
async def revoke_resource_grant(
    resource_id: UUID,
    revision_id: UUID,
    grant_snapshot_id: UUID,
    authorization: str | None = Header(None),
) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        _resources.get(resource_id, owner)
        _resources.resolve_ref(resource_id, revision_id, owner, None)
        _resources.revoke_grant(revision_id, grant_snapshot_id, owner)
        return {"status": "revoked", "grant_snapshot_id": str(grant_snapshot_id)}
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/{resource_id}/revisions/{revision_id}/resolve-ref", response_model=ResourceRef)
async def resolve_resource_ref(
    resource_id: UUID,
    revision_id: UUID,
    grant_snapshot_id: UUID | None = None,
    authorization: str | None = Header(None),
) -> ResourceRef:
    owner = _resolve_owner(authorization)
    try:
        return _resources.resolve_ref(resource_id, revision_id, owner, grant_snapshot_id)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.post("/artifacts/resources/{resource_id}/revisions/{revision_id}/evaluate-entitlement", response_model=EntitlementDecision)
async def evaluate_entitlement(
    resource_id: UUID,
    revision_id: UUID,
    body: EvaluateEntitlementRequest,
    authorization: str | None = Header(None),
) -> EntitlementDecision:
    owner = _resolve_owner(authorization)
    try:
        return _resources.evaluate_entitlement(
            resource_id,
            revision_id,
            owner,
            body.action,
            body.grant_snapshot_id,
        )
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.get("/artifacts/resources/{resource_id}/revisions/{revision_id}", response_model=ResourceRevision)
async def get_resource_revision(
    resource_id: UUID,
    revision_id: UUID,
    authorization: str | None = Header(None),
) -> ResourceRevision:
    """Read-only ResourceRevision accessor.

    Exists so the workbench CAS UI can confirm the frozen
    ``content_artifact_version_id`` before issuing a write.  The
    endpoint never accepts a body and never mutates the row.
    """
    owner = _resolve_owner(authorization)
    try:
        return _resources.assert_revision_immutable(revision_id, owner)
    except (NotFoundError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


# ===========================================================================
# Blob endpoints (TF-OPS-003 Foundation)
# ===========================================================================


@router.post("/blobs/upload-sessions")
async def start_blob_upload(
    body: BlobStartUploadRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        upload = _blobs.start_upload(
            owner,
            expected_size_bytes=body.expected_size_bytes,
            expected_content_hash=body.expected_content_hash,
            media_type=body.media_type,
            idempotency_key=body.idempotency_key,
            part_state=body.part_state,
        )
        return upload.model_dump(mode="json")
    except (ValidationError_, ConflictError, CrossOwnerError) as exc:
        raise _safe_error_response(exc)


@router.post("/blobs/upload-sessions/{session_id}/complete")
async def complete_blob_upload(
    session_id: UUID,
    body: BlobCompleteRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    payload = body.content_base64 or ""
    try:
        data = _decode_payload(payload)
    except ValidationError_ as exc:
        raise _safe_error_response(exc)
    declared_size = body.declared_size if body.declared_size is not None else len(data)
    declared_hash = body.declared_hash or sha256_hex(data)
    try:
        blob = _blobs.complete_upload(
            session_id,
            owner,
            io.BytesIO(data),
            declared_size=declared_size,
            declared_hash=declared_hash,
            durability_receipt=body.durability_receipt,
        )
    except (ValidationError_, ConflictError, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)
    return blob.model_dump(mode="json")


@router.post("/blobs/upload-sessions/{session_id}/abort")
async def abort_blob_upload(
    session_id: UUID,
    body: BlobAbortRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    if body.session_id != session_id:
        raise HTTPException(status_code=422, detail={
            "error": {"code": "VALIDATION_ERROR", "message": "session_id mismatch"},
        })
    try:
        upload = _blobs.abort_upload(session_id, owner)
        return upload.model_dump(mode="json")
    except (ConflictError, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)


@router.get("/blobs/{blob_id}")
async def get_blob(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        return _blobs.get(blob_id, owner).model_dump(mode="json")
    except (CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)


@router.get("/blobs/{blob_id}/content")
async def read_blob_content(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        data = _blobs.read_bytes(blob_id, owner)
    except (ValidationError_, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)
    return {
        "blob_id": str(blob_id),
        "size_bytes": len(data),
        "content_base64": data.hex(),
    }


@router.get("/blobs/{blob_id}/references")
async def blob_references(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        refs = _blobs.references_for(blob_id, owner)
    except (CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)
    return {kind: [str(rid) for rid in ids] for kind, ids in refs.items()}


@router.post("/blobs/{blob_id}/deletion-check")
async def blob_deletion_check(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Server-side guard before any destructive lifecycle action.

    Returns the structured references if the action is refused.
    """
    owner = _resolve_owner(authorization)
    try:
        _blobs.assert_lifecycle_allowed(blob_id, owner, "delete")
    except (ValidationError_, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)
    return {"allowed": True, "blob_id": str(blob_id)}


@router.post("/blobs/{blob_id}/deletion-pending")
async def mark_blob_deletion_pending(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    owner = _resolve_owner(authorization)
    try:
        _blobs.assert_lifecycle_allowed(blob_id, owner, "delete")
        blob = _blobs.mark_deletion_pending(blob_id, owner)
        return blob.model_dump(mode="json")
    except (ValidationError_, ConflictError, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)


@router.delete("/blobs/{blob_id}")
async def finalize_blob_deletion(blob_id: UUID, authorization: str | None = Header(None)) -> dict[str, str]:
    owner = _resolve_owner(authorization)
    try:
        _blobs.assert_lifecycle_allowed(blob_id, owner, "delete")
        _blobs.finalize_delete(blob_id, owner)
    except (ValidationError_, ConflictError, CrossOwnerError, NotFoundError) as exc:
        raise _safe_error_response(exc)
    return {"status": "deleted", "blob_id": str(blob_id)}


@router.post("/blobs/rebuild-reference-index")
async def rebuild_blob_reference_index(authorization: str | None = Header(None)) -> dict[str, int]:
    owner = _resolve_owner(authorization)
    written = _blobs.rebuild_reference_index(owner)
    return {"rows_written": written}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _decode_payload(value: str) -> bytes:
    import base64
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001 — translated
        raise ValidationError_("content_base64 不是合法 base64") from exc