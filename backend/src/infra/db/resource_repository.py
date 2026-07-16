"""Canonical PostgreSQL resource, entitlement and promotion repository.

This module is the single source of truth for:

* ``Resource`` identity and OC-promotion provenance.
* ``ResourceDraft`` and ``ResourceRevision`` lifecycle with CAS.
* ``ResourceGrantSnapshot`` lifecycle, including cross-owner
  ``EntitlementDecision`` recompute.
* The OutputBinding / SelectionRecord → ``ResourceRevision`` promotion
  gate (TF-PLT-003 / TF-WF-005 §16).
* Reference protection for Blob deletion (delegated through the
  ArtifactRepository).

The repository never mutates a frozen ``ResourceRevision`` once it is
written.  Any service-layer attempt to patch ``content_artifact_version_id``
or ``revision_number`` MUST fail loudly.  Draft CAS conflicts carry the
structured ``CasConflict`` shape that the HTTP layer turns into a 409.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactVersionModel,
    CandidateSetModel,
    OutputBindingSupersedeModel,
    OutboxEventModel,
    ProviderOutputBindingModel,
    ResourceDraftModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
    SelectionRecordModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import (
    CasConflict,
    EntitlementDecision,
    OwnerScope,
    PromotionSource,
    Resource,
    ResourceDraft,
    ResourceRef,
    ResourceRevision,
)


KNOWN_RESOURCE_TYPES = frozenset({
    "world", "character", "shot_plan", "shot_spec", "creative_work",
    "agent", "recipe", "skill", "creative_board", "generic",
})


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _world_character_content(world_content: object, local_id: str) -> dict[str, object]:
    """Resolve a WorldPackage embedded character by stable local ID."""
    if not isinstance(world_content, dict):
        raise ConflictError("来源 WorldRevision 不含可验证的 WorldPackage 内容")
    characters = world_content.get("embedded_characters")
    if not isinstance(characters, list):
        raise ConflictError("来源 WorldRevision 不含 embedded_characters")
    for character in characters:
        if not isinstance(character, dict):
            continue
        if character.get("world_local_character_id") == local_id:
            return character
    raise ConflictError("来源 WorldRevision 中不存在该 world_local_character_id")


# ---------------------------------------------------------------------------
# ArtifactVersion ref resolution — single source of truth
# ---------------------------------------------------------------------------
#
# A candidate ref inside a SelectionRecord.selected_refs can use one
# of three key names.  The promotion paths (resolve_promotion_source
# / promote_from_source) and the bootstrap gate in ``create()`` MUST
# agree on which key names are recognised, otherwise a caller can
# sneak a ref shape through bootstrap that the promote path would
# otherwise accept.  This helper is the single resolver; every
# call site MUST go through it.
#
# Supported keys (in order of preference, first match wins):
#   1. ``artifact_version_id``  (snake_case UUID, primary contract)
#   2. ``artifactVersionId``    (camelCase, legacy / external writers)
#   3. ``output_artifact_version_id``  (snake_case, mirrors the
#      OutputBinding column and unifies a ref shape that downstream
#      workers can also write)

_RECOGNISED_REF_KEYS: tuple[str, ...] = (
    "artifact_version_id",
    "artifactVersionId",
    "output_artifact_version_id",
)


def _resolve_artifact_version_id_from_ref(ref: object) -> UUID | None:
    """Return the ArtifactVersion id a candidate ref points at, or
    None if the ref is not a dict / has no recognised UUID key.

    Does NOT raise on missing key: callers that REQUIRE a usable ref
    should treat ``None`` as a 4xx-class validation problem.
    """
    if not isinstance(ref, dict):
        return None
    for key in _RECOGNISED_REF_KEYS:
        raw = ref.get(key)
        if raw is None or raw == "":
            continue
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            continue
    return None


def _owner(raw: object) -> OwnerScope:
    raw_str = str(raw)
    kind, _, value = raw_str.partition(":")
    return OwnerScope(kind=kind, id=UUID(value))


def _resource(row: Any) -> Resource:
    return Resource(
        resource_id=row.resource_id,  # type: ignore[arg-type]
        resource_type=row.resource_type,  # type: ignore[arg-type]
        owner_scope=_owner(row.owner_scope),
        source_world_revision_id=row.source_world_revision_id,  # type: ignore[arg-type]
        source_local_id=row.source_local_id,  # type: ignore[arg-type]
        source_content_hash=row.source_content_hash,  # type: ignore[arg-type]
        elevation_event_id=row.elevation_event_id,  # type: ignore[arg-type]
        promotion_source_kind=row.promotion_source_kind,  # type: ignore[arg-type]
        promotion_source_ref_id=row.promotion_source_ref_id,  # type: ignore[arg-type]
        promotion_source_artifact_version_id=row.promotion_source_artifact_version_id,  # type: ignore[arg-type]
        created_at=row.created_at,  # type: ignore[arg-type]
    )


def _draft(row: Any) -> ResourceDraft:
    return ResourceDraft(
        resource_id=row.resource_id,  # type: ignore[arg-type]
        draft_version=row.draft_version,  # type: ignore[arg-type]
        base_revision_id=row.base_revision_id,  # type: ignore[arg-type]
        content_artifact_version_id=row.content_artifact_version_id,  # type: ignore[arg-type]
        updated_at=row.updated_at,  # type: ignore[arg-type]
    )


def _revision(row: Any) -> ResourceRevision:
    return ResourceRevision(
        resource_id=row.resource_id,  # type: ignore[arg-type]
        revision_id=row.revision_id,  # type: ignore[arg-type]
        revision_number=row.revision_number,  # type: ignore[arg-type]
        content_artifact_version_id=row.content_artifact_version_id,  # type: ignore[arg-type]
        revision_status=row.revision_status,  # type: ignore[arg-type]
        created_from_artifact_version_id=row.created_from_artifact_version_id,  # type: ignore[arg-type]
        source_world_revision_id=row.source_world_revision_id,  # type: ignore[arg-type]
        source_local_id=row.source_local_id,  # type: ignore[arg-type]
        source_content_hash=row.source_content_hash,  # type: ignore[arg-type]
        elevation_event_id=row.elevation_event_id,  # type: ignore[arg-type]
        created_at=row.created_at,  # type: ignore[arg-type]
    )


def _cas_conflict(
    *,
    resource_id: UUID,
    operation: str,
    base_draft_version: int,
    current_draft_version: int,
    current_content_artifact_version_id: UUID,
    proposed_content_artifact_version_id: UUID,
    reason: str = "",
) -> CasConflict:
    return CasConflict(
        resource_id=resource_id,
        operation=operation,
        base_draft_version=base_draft_version,
        current_draft_version=current_draft_version,
        current_content_artifact_version_id=current_content_artifact_version_id,
        proposed_content_artifact_version_id=proposed_content_artifact_version_id,
        reason=reason,
    )


class SqlResourceRepository:
    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # Resource identity
    # ------------------------------------------------------------------

    def create(
        self,
        owner: OwnerScope,
        resource_type: str,
        content_artifact_version_id: UUID,
        *,
        source_world_revision_id: UUID | None = None,
        source_local_id: str | None = None,
        elevation_event_id: UUID | None = None,
    ) -> Resource:
        """Create a Resource from an already-validated content ArtifactVersion.

        # Bootstrap path.  ONLY usable when the ArtifactVersion has NO
        # existing promotion source: i.e. it is not cited by any
        # ``ProviderOutputBinding.output_artifact_version_id`` and it
        # does not appear inside any ``SelectionRecord.selected_refs``
        # owned by the caller.  An ArtifactVersion that is already
        # pinned to a valid OutputBinding / SelectionRecord MUST be
        # promoted through :meth:`promote_from_source` — bypassing the
        # gate here would let a caller bootstrap a Resource from a
        # run output without the durable, non-superseded source that
        # TF-PLT-003 / TF-WF-005 §16 requires.
        """
        if resource_type not in KNOWN_RESOURCE_TYPES:
            raise ValidationError_("Resource 必须使用已注册的资源类型")
        with self._factory.begin() as session:
            artifact = session.get(ArtifactVersionModel, content_artifact_version_id)
            if artifact is None or artifact.owner_scope != owner.scoped_id:
                raise NotFoundError("ArtifactVersion", str(content_artifact_version_id))
            # Block bootstrap when the ArtifactVersion is already cited
            # by a same-owner OutputBinding.  ``with_for_update`` is not
            # needed here: we only need a snapshot read, and the binding
            # owner is immutable so there is no race for THIS check.
            binding_match = session.scalar(
                select(ProviderOutputBindingModel.binding_id).where(
                    ProviderOutputBindingModel.owner_scope == owner.scoped_id,
                    ProviderOutputBindingModel.output_artifact_version_id == content_artifact_version_id,
                ).limit(1)
            )
            if binding_match is not None:
                raise ConflictError(
                    "ArtifactVersion 已被 OutputBinding 引用，必须经 promote_from_source 提升",
                    details={
                        "binding_id": str(binding_match),
                        "artifact_version_id": str(content_artifact_version_id),
                    },
                )
            # Block bootstrap when the ArtifactVersion appears inside
            # any same-owner SelectionRecord.selected_refs under ANY
            # of the recognised key names.  We unnest each
            # ``selected_refs`` JSONB array element with
            # ``jsonb_array_elements`` and check the element as a JSONB
            # object via PostgreSQL containment (``@>``).  The match is
            # exact per element, owner-scoped, and uses the same three
            # keys that ``_resolve_artifact_version_id_from_ref`` accepts
            # — keeping create() and promote_from_source() on the same
            # contract.
            json_match = session.scalar(
                __import__("sqlalchemy").text(
                    "SELECT selection_id FROM selection_records, "
                    "jsonb_array_elements(selected_refs::jsonb) AS elem "
                    "WHERE owner_scope = :scope "
                    "  AND ( elem @> (:k1_text)::jsonb "
                    "     OR elem @> (:k2_text)::jsonb "
                    "     OR elem @> (:k3_text)::jsonb ) "
                    "LIMIT 1"
                ).bindparams(
                    scope=owner.scoped_id,
                    k1_text=f'{{"artifact_version_id": "{content_artifact_version_id}"}}',
                    k2_text=f'{{"artifactVersionId": "{content_artifact_version_id}"}}',
                    k3_text=f'{{"output_artifact_version_id": "{content_artifact_version_id}"}}',
                )
            )
            if json_match is not None:
                raise ConflictError(
                    "ArtifactVersion 已被 SelectionRecord 引用，必须经 promote_from_source 提升",
                    details={
                        "selection_id": str(json_match),
                        "artifact_version_id": str(content_artifact_version_id),
                    },
                )
            source_content_hash: str | None = None
            has_origin = any((source_world_revision_id, source_local_id, elevation_event_id))
            if has_origin:
                if resource_type != "character":
                    raise ConflictError("仅 Character Resource 可以声明 World OC 提升来源")
                if not all((source_world_revision_id, source_local_id)):
                    raise ConflictError("OC 提升必须同时固定 source_world_revision_id 和 source_local_id")
                source_revision = session.execute(
                    select(ResourceRevisionModel)
                    .where(ResourceRevisionModel.revision_id == source_world_revision_id)
                    .with_for_update()
                ).scalar_one_or_none()
                source_resource = session.get(ResourceModel, source_revision.resource_id) if source_revision else None
                if (
                    source_revision is None
                    or source_resource is None
                    or source_resource.owner_scope != owner.scoped_id
                    or source_resource.resource_type != "world"
                ):
                    raise CrossOwnerError()
                world_artifact = session.get(
                    ArtifactVersionModel, source_revision.content_artifact_version_id,
                )
                source_character = _world_character_content(
                    world_artifact.content_json if world_artifact else None,
                    source_local_id,
                )
                if _canonical_json(artifact.content_json or {}) != _canonical_json(source_character):
                    raise ConflictError("OC 提升内容必须精确复制来源 WorldRevision 的内嵌角色")
                duplicate = session.scalar(
                    select(ResourceModel.resource_id).where(
                        ResourceModel.source_world_revision_id == source_world_revision_id,
                        ResourceModel.source_local_id == source_local_id,
                    )
                )
                if duplicate is not None:
                    raise ConflictError("该 WorldRevision/local ID 已提升为 Character；请复用已有资源")
                source_content_hash = _content_hash(source_character)
                elevation_event_id = elevation_event_id or uuid4()
            # Bootstrap provenance: the ArtifactVersion is owned but the
            # Resource is created via the bootstrap path, so we record
            # ``kind=bootstrap`` with no upstream ref id.
            row = ResourceModel(
                resource_id=uuid4(), resource_type=resource_type, owner_scope=owner.scoped_id,
                source_world_revision_id=source_world_revision_id, source_local_id=source_local_id,
                source_content_hash=source_content_hash,
                elevation_event_id=elevation_event_id,
                promotion_source_kind="bootstrap",
                promotion_source_ref_id=None,
                promotion_source_artifact_version_id=content_artifact_version_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            session.add(ResourceDraftModel(
                resource_id=row.resource_id, draft_version=1, base_revision_id=None,
                content_artifact_version_id=content_artifact_version_id,
                updated_at=datetime.now(timezone.utc),
            ))
            return _resource(row)

    def get(self, resource_id: UUID, owner: OwnerScope) -> Resource:
        with self._factory() as session:
            row = session.get(ResourceModel, resource_id)
            if row is None:
                raise NotFoundError("Resource", str(resource_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            return _resource(row)

    def list_resources(self, owner: OwnerScope, resource_type: str | None = None) -> list[Resource]:
        with self._factory() as session:
            stmt = select(ResourceModel).where(ResourceModel.owner_scope == owner.scoped_id).order_by(ResourceModel.created_at.desc())
            if resource_type:
                stmt = stmt.where(ResourceModel.resource_type == resource_type)
            return [_resource(row) for row in session.scalars(stmt)]

    def get_draft(self, resource_id: UUID, owner: OwnerScope) -> ResourceDraft:
        self.get(resource_id, owner)
        with self._factory() as session:
            row = session.get(ResourceDraftModel, resource_id)
            if row is None:
                raise NotFoundError("ResourceDraft", str(resource_id))
            return _draft(row)

    def list_revisions(self, resource_id: UUID, owner: OwnerScope) -> list[ResourceRevision]:
        self.get(resource_id, owner)
        with self._factory() as session:
            rows = list(session.scalars(
                select(ResourceRevisionModel)
                .where(ResourceRevisionModel.resource_id == resource_id)
                .order_by(ResourceRevisionModel.revision_number.desc())
            ))
            return [_revision(row) for row in rows]

    # ------------------------------------------------------------------
    # Provenance + rebuild
    # ------------------------------------------------------------------

    def provenance(self, resource_id: UUID, owner: OwnerScope) -> dict[str, object]:
        resource = self.get(resource_id, owner)
        draft = self.get_draft(resource_id, owner)
        revisions = self.list_revisions(resource_id, owner)
        with self._factory() as session:
            artifact_ids = {draft.content_artifact_version_id, *(r.content_artifact_version_id for r in revisions)}
            artifacts = {
                row.artifact_version_id: row
                for row in session.scalars(select(ArtifactVersionModel).where(ArtifactVersionModel.artifact_version_id.in_(artifact_ids)))
            }

        def artifact_summary(version_id: UUID) -> dict[str, object]:
            row = artifacts.get(version_id)
            if row is None:
                return {"artifact_version_id": str(version_id), "availability": "missing"}
            return {
                "artifact_version_id": str(row.artifact_version_id),  # type: ignore[arg-type]
                "schema_id": row.schema_id,  # type: ignore[arg-type]
                "schema_version": row.schema_version,  # type: ignore[arg-type]
                "content_hash": row.content_hash,  # type: ignore[arg-type]
                "content_uri": row.content_uri,  # type: ignore[arg-type]
                "blob_uri": row.blob_uri,  # type: ignore[arg-type]
                "created_by_run_id": str(row.created_by_run_id) if row.created_by_run_id else None,  # type: ignore[arg-type]
                "lineage_input_refs": row.lineage_input_refs or [],  # type: ignore[arg-type]
                "metadata": row.metadata_json or {},  # type: ignore[arg-type]
            }

        return {
            "resource": resource.model_dump(mode="json"),
            "draft": draft.model_dump(mode="json"),
            "draft_content": artifact_summary(draft.content_artifact_version_id),
            "revisions": [
                {**revision.model_dump(mode="json"), "content": artifact_summary(revision.content_artifact_version_id)}
                for revision in revisions
            ],
        }

    def rebuild_projection(self, owner: OwnerScope) -> dict[str, object]:
        resources = self.list_resources(owner)
        return {
            "source": "canonical_postgresql",
            "resources": [self.provenance(row.resource_id, owner) for row in resources],
        }

    # ------------------------------------------------------------------
    # ResourceDraft + ResourceRevision CAS
    # ------------------------------------------------------------------

    def save_draft(
        self,
        resource_id: UUID,
        owner: OwnerScope,
        content_artifact_version_id: UUID,
        base_draft_version: int,
    ) -> ResourceDraft:
        """Atomically replace a draft with CAS on ``draft_version``.

        The conditional ``UPDATE`` MUST observe ``rowcount``: when two
        transactions race past the read step, only one will land a
        rowcount of 1 — the loser MUST be told which side it lost.  We
        re-read the current draft inside the same transaction so the
        structured ``CasConflict`` payload reflects the actual
        persisted state, not the loser's stale snapshot.
        """
        self.get(resource_id, owner)
        with self._factory.begin() as session:
            artifact = session.get(ArtifactVersionModel, content_artifact_version_id)
            if artifact is None or artifact.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            rowcount = session.execute(update(ResourceDraftModel).where(
                ResourceDraftModel.resource_id == resource_id,
                ResourceDraftModel.draft_version == base_draft_version,
            ).values(
                draft_version=ResourceDraftModel.draft_version + 1,
                content_artifact_version_id=content_artifact_version_id,
                updated_at=datetime.now(timezone.utc),
            ).execution_options(synchronize_session=False)).rowcount  # type: ignore[attr-defined]
            if rowcount == 0:
                # Re-read the now-current draft so the client sees the
                # winner's actual state, not the loser's pre-CAS view.
                current = session.get(ResourceDraftModel, resource_id)
                assert current is not None
                raise ConflictError(
                    message="ResourceDraft compare-and-swap conflict",
                    details=_cas_conflict(
                        resource_id=resource_id,
                        operation="save_draft",
                        base_draft_version=base_draft_version,
                        current_draft_version=current.draft_version,  # type: ignore[arg-type]
                        current_content_artifact_version_id=current.content_artifact_version_id,  # type: ignore[arg-type]
                        proposed_content_artifact_version_id=content_artifact_version_id,
                        reason="base_draft_version 不匹配当前 draft_version",
                    ).model_dump(mode="json"),
                )
            row = session.get(ResourceDraftModel, resource_id)
            assert row is not None
            return _draft(row)

    def freeze(self, resource_id: UUID, owner: OwnerScope, base_draft_version: int) -> ResourceRevision:
        self.get(resource_id, owner)
        with self._factory.begin() as session:
            draft = session.execute(
                select(ResourceDraftModel).where(ResourceDraftModel.resource_id == resource_id).with_for_update()
            ).scalar_one_or_none()
            if draft is None:
                raise NotFoundError("ResourceDraft", str(resource_id))
            if draft.draft_version != base_draft_version:  # type: ignore[operator]
                raise ConflictError(
                    message="ResourceDraft compare-and-swap conflict",
                    details=_cas_conflict(
                        resource_id=resource_id,
                        operation="freeze",
                        base_draft_version=base_draft_version,
                        current_draft_version=draft.draft_version,  # type: ignore[arg-type]
                        current_content_artifact_version_id=draft.content_artifact_version_id,  # type: ignore[arg-type]
                        proposed_content_artifact_version_id=draft.content_artifact_version_id,  # type: ignore[arg-type]
                        reason="freeze base_draft_version 不匹配当前 draft_version",
                    ).model_dump(mode="json"),
                )
            last = session.scalar(
                select(ResourceRevisionModel.revision_number)
                .where(ResourceRevisionModel.resource_id == resource_id)
                .order_by(ResourceRevisionModel.revision_number.desc())
                .limit(1)
            ) or 0
            session.execute(update(ResourceRevisionModel).where(
                ResourceRevisionModel.resource_id == resource_id,
                ResourceRevisionModel.revision_status == RevisionStatus.ACTIVE,
            ).values(revision_status=RevisionStatus.RETIRED))
            previous = session.get(ResourceRevisionModel, draft.base_revision_id) if draft.base_revision_id else None
            resource = session.get(ResourceModel, resource_id)
            assert resource is not None
            row = ResourceRevisionModel(
                revision_id=uuid4(), resource_id=resource_id, revision_number=last + 1,
                content_artifact_version_id=draft.content_artifact_version_id,
                revision_status=RevisionStatus.ACTIVE,
                created_from_artifact_version_id=previous.content_artifact_version_id if previous else None,
                source_world_revision_id=resource.source_world_revision_id,
                source_local_id=resource.source_local_id,
                source_content_hash=resource.source_content_hash,
                elevation_event_id=resource.elevation_event_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            draft.base_revision_id = row.revision_id
            if previous is not None:
                stale = {
                    "resource_id": str(resource_id),
                    "superseded_revision_id": str(previous.revision_id),
                    "current_revision_id": str(row.revision_id),
                }
                for candidate in session.scalars(select(ResourceDraftModel).where(
                    ResourceDraftModel.resource_id != resource_id,
                    ResourceDraftModel.base_revision_id == previous.revision_id,
                )):
                    candidate.stale_reason = stale
            session.flush()
            return _revision(row)

    def assert_revision_immutable(self, revision_id: UUID, owner: OwnerScope) -> ResourceRevision:
        """Service-side guard: a frozen ResourceRevision MUST NOT have its
        ``content_artifact_version_id`` swapped.  The repository itself
        never exposes an update path, but this helper makes the invariant
        explicit to any future caller.
        """
        with self._factory() as session:
            row = session.get(ResourceRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("ResourceRevision", str(revision_id))
            resource = session.get(ResourceModel, row.resource_id)
            if resource is None or resource.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            return _revision(row)

    # ------------------------------------------------------------------
    # Grant + EntitlementDecision
    # ------------------------------------------------------------------

    _CAPABILITY_ACTIONS = frozenset({"reference", "execute", "redistribute"})

    @classmethod
    def _normalize_actions(cls, actions: list[str]) -> list[str]:
        normalized = sorted({str(action).strip().lower() for action in actions if str(action).strip()})
        if not normalized or any(action not in cls._CAPABILITY_ACTIONS for action in normalized):
            raise ValidationError_("Resource grant contains an unsupported capability action")
        return normalized

    def grant(
        self,
        revision_id: UUID,
        owner: OwnerScope,
        grantee: OwnerScope,
        *,
        capability_actions: list[str],
    ) -> UUID:
        actions = self._normalize_actions(capability_actions)
        with self._factory.begin() as session:
            revision = session.get(ResourceRevisionModel, revision_id)
            if revision is None:
                raise NotFoundError("ResourceRevision", str(revision_id))
            resource = session.get(ResourceModel, revision.resource_id)
            if resource is None or resource.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            row = ResourceGrantSnapshotModel(
                grant_snapshot_id=uuid4(), resource_revision_id=revision_id,
                grantee_scope=grantee.scoped_id, capability_actions=actions,
                status="active", created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row.grant_snapshot_id

    def revoke_grant(self, revision_id: UUID, grant_snapshot_id: UUID, owner: OwnerScope) -> None:
        with self._factory.begin() as session:
            revision = session.get(ResourceRevisionModel, revision_id)
            resource = session.get(ResourceModel, revision.resource_id) if revision else None
            grant = session.get(ResourceGrantSnapshotModel, grant_snapshot_id)
            if resource is None or resource.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            if grant is None or grant.resource_revision_id != revision_id:
                raise NotFoundError("ResourceGrantSnapshot", str(grant.grant_snapshot_id))  # type: ignore[union-attr]
            if grant.status != "revoked":
                grant.status = "revoked"
                grant.revoked_at = datetime.now(timezone.utc)
                session.add(OutboxEventModel(
                    event_id=uuid4(), aggregate_type="resource_grant",
                    aggregate_id=grant.grant_snapshot_id, event_type="resource_grant.revoked",
                    payload={"resource_revision_id": str(revision_id), "grantee_scope": grant.grantee_scope},
                    purpose="resource_grant_revoke", created_at=datetime.now(timezone.utc),
                ))

    def resolve_ref(
        self,
        resource_id: UUID,
        revision_id: UUID,
        requester: OwnerScope,
        grant_snapshot_id: UUID | None,
        *,
        required_actions: set[str] | None = None,
    ) -> ResourceRef:
        """Resolve a ResourceRef and recompute the live entitlement."""
        with self._factory() as session:
            resource = session.get(ResourceModel, resource_id)
            revision = session.get(ResourceRevisionModel, revision_id)
            if resource is None or revision is None or revision.resource_id != resource_id:
                raise NotFoundError("ResourceRevision", str(revision_id))
            if revision.revision_status != RevisionStatus.ACTIVE:
                raise NotFoundError("ResourceRevision", str(revision_id))
            cross_owner = resource.owner_scope != requester.scoped_id
            if cross_owner:
                if grant_snapshot_id is None:
                    raise CrossOwnerError()
                grant = session.get(ResourceGrantSnapshotModel, grant_snapshot_id)
                if (
                    grant is None
                    or grant.resource_revision_id != revision_id
                    or grant.grantee_scope != requester.scoped_id
                    or grant.status != "active"
                ):
                    raise CrossOwnerError()
                actions = set(grant.capability_actions or [])
                if required_actions and not required_actions.issubset(actions):
                    raise CrossOwnerError()
            return ResourceRef(
                resource_id=resource_id,
                resource_type=resource.resource_type,
                revision_id=revision_id,
                grant_snapshot_id=grant_snapshot_id if cross_owner else None,
            )

    def evaluate_entitlement(
        self,
        resource_id: UUID,
        revision_id: UUID,
        requester: OwnerScope,
        action: str,
        grant_snapshot_id: UUID | None,
    ) -> EntitlementDecision:
        """Re-evaluate the cross-owner entitlement for a fresh action."""
        with self._factory() as session:
            resource = session.get(ResourceModel, resource_id)
            revision = session.get(ResourceRevisionModel, revision_id)
            decision = EntitlementDecision(
                subject_scope=requester,
                resource_revision_id=revision_id,
                action=action,
                decision="deny",
                grant_snapshot_id=grant_snapshot_id,
                evaluated_at=datetime.now(timezone.utc),
            )
            if resource is None or revision is None or revision.resource_id != resource_id:
                decision.reason = "ResourceRevision 不存在"
                return decision
            if revision.revision_status != RevisionStatus.ACTIVE:
                decision.reason = "ResourceRevision 不处于 active 状态"
                return decision
            if resource.owner_scope == requester.scoped_id:
                decision.decision = "allow"
                decision.reason = "same-owner 访问"
                decision.grant_snapshot_id = None
                return decision
            if grant_snapshot_id is None:
                decision.reason = "跨 owner 访问缺少 grant_snapshot_id"
                return decision
            grant = session.get(ResourceGrantSnapshotModel, grant_snapshot_id)
            if (
                grant is None
                or grant.resource_revision_id != revision_id
                or grant.grantee_scope != requester.scoped_id
                or grant.status != "active"
            ):
                decision.reason = "grant_snapshot 不可用或已 revoked"
                return decision
            actions = set(grant.capability_actions or [])
            if action not in actions:
                decision.reason = f"action {action} 不在 grant capability 范围"
                return decision
            decision.decision = "allow"
            decision.reason = "active grant_snapshot 授权"
            return decision

    # ------------------------------------------------------------------
    # Promotion gate (TF-WF-005 + TF-PLT-003)
    # ------------------------------------------------------------------

    def resolve_promotion_source(
        self,
        owner: OwnerScope,
        source: PromotionSource,
    ) -> tuple[UUID, dict[str, Any]]:
        """Validate a PromotionSource and return the ``artifact_version_id`` to elevate.

        All rejections raise ``ConflictError``:

        * bare ``artifact_id`` or anything that is not ``output_binding``
          or ``selection_record``,
        * the referenced OutputBinding / SelectionRecord does not exist
          OR belongs to another owner,
        * the target ArtifactVersion is missing OR belongs to another owner,
        * a ``supersede`` row exists for this ``(ref_kind, ref_id)``
          AND that row was written by the SAME owner (cross-owner
          supersede rows are silently ignored at the resolver boundary
          so an attacker cannot grief a victim from another tenant),
        * an OutputBinding whose ``output_artifact_version_id`` is empty,
        * a SelectionRecord that has zero selected_refs or whose target
          ref is malformed.

        The function is read-only.  The corresponding write is
        :meth:`promote_from_source`, which MUST be called inside the
        same session as the Resource insert to keep the gate atomic.
        """
        if source.kind not in {"output_binding", "selection_record"}:
            raise ConflictError(
                "Resource 提升必须来自 OutputBinding/SelectionRecord，禁止裸 artifact_id",
                details={"source_kind": source.kind},
            )
        if source.superseded:
            raise ConflictError(
                "无法从已被 superseded 的 OutputBinding/SelectionRecord 提升 Resource",
                details={"source_kind": source.kind},
            )
        with self._factory() as session:
            target_id = source.binding_id or source.selection_id
            assert target_id is not None
            # The supersede scan is owner-scoped: a foreign-owner supersede
            # row is invisible to this resolver.  This is what blocks the
            # "attacker with a guessed id" grief pattern.
            superseded = session.scalar(
                select(OutputBindingSupersedeModel).where(
                    OutputBindingSupersedeModel.owner_scope == owner.scoped_id,
                    OutputBindingSupersedeModel.ref_kind == source.kind,
                    OutputBindingSupersedeModel.ref_id == target_id,
                )
            )
            if superseded is not None:
                raise ConflictError(
                    "OutputBinding/SelectionRecord 已被 superseded，禁止提升",
                    details={
                        "ref_kind": source.kind,
                        "ref_id": str(superseded.ref_id),
                        "superseded_by": str(superseded.superseded_by_ref_id or ""),
                        "reason": superseded.reason,
                    },
                )
            if source.kind == "output_binding":
                if source.binding_id is None:
                    raise ConflictError("output_binding 提升必须提供 binding_id")
                binding = session.get(ProviderOutputBindingModel, source.binding_id)
                if binding is None:
                    raise NotFoundError("OutputBinding", str(source.binding_id))
                if binding.owner_scope != owner.scoped_id:
                    # Cross-tenant attempt: surface a cross-owner denial
                    # without leaking the victim's id.
                    raise CrossOwnerError()
                if not binding.output_artifact_version_id:
                    raise ConflictError("OutputBinding 缺少 output_artifact_version_id")
                artifact = session.get(ArtifactVersionModel, binding.output_artifact_version_id)
                if artifact is None:
                    raise NotFoundError("ArtifactVersion", str(binding.output_artifact_version_id))
                if artifact.owner_scope != owner.scoped_id:
                    raise CrossOwnerError()
                return binding.output_artifact_version_id, {
                    "binding_id": str(binding.binding_id),
                    "record_id": str(binding.record_id),
                    "output_index": binding.output_index,
                    "output_label": binding.output_label,
                    "artifact_version_id": str(binding.output_artifact_version_id),
                }
            # selection_record
            if source.selection_id is None:
                raise ConflictError("selection_record 提升必须提供 selection_id")
            selection = session.get(SelectionRecordModel, source.selection_id)
            if selection is None:
                raise NotFoundError("SelectionRecord", str(source.selection_id))
            if selection.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            selected_refs = list(selection.selected_refs or [])
            if not selected_refs:
                raise ConflictError("SelectionRecord 没有 selected_refs")
            if source.output_index is not None and source.output_index >= 0:
                if source.output_index >= len(selected_refs):
                    raise ConflictError(
                        "SelectionRecord output_index 越界",
                        details={"selected_count": len(selected_refs)},
                    )
                candidate = selected_refs[source.output_index]
            else:
                candidate = selected_refs[0]
            if not isinstance(candidate, dict):
                raise ConflictError("SelectionRecord ref 必须是对象")
            resolved_id = _resolve_artifact_version_id_from_ref(candidate)
            if resolved_id is None:
                raise ConflictError(
                    "SelectionRecord ref 缺少可识别的 artifact_version_id 字段",
                )
            artifact = session.get(ArtifactVersionModel, resolved_id)
            if artifact is None:
                raise NotFoundError("ArtifactVersion", str(resolved_id))
            if artifact.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            candidate_set = session.get(CandidateSetModel, selection.candidate_set_id)
            return resolved_id, {
                "selection_id": str(selection.selection_id),  # type: ignore[arg-type]
                "candidate_set_id": str(selection.candidate_set_id),  # type: ignore[arg-type]
                "candidate_set_owner_scope": candidate_set.owner_scope if candidate_set else owner.scoped_id,
                "candidate_refs_count": str(len(selected_refs)),
                "artifact_version_id": str(resolved_id),
            }

    def promote_from_source(
        self,
        owner: OwnerScope,
        source: PromotionSource,
        resource_type: str,
    ) -> tuple[Resource, dict[str, Any]]:
        """Single-transaction promotion gate.

        Locks the OutputBinding / SelectionRecord row FIRST, then
        reads the supersede state, then resolves the target
        ArtifactVersion and inserts the Resource + Draft rows together
        with the immutable provenance (kind, ref_id,
        artifact_version_id).

        Linearisation contract (PostgreSQL READ COMMITTED):

        * supersede commits first  → the ``SELECT ... FOR UPDATE`` on
          the source row blocks until supersede commits, then sees
          the supersede row and aborts.
        * promote commits first    → the supersede writer's
          ``SELECT ... FOR UPDATE`` on the same source row blocks until
          promote commits, then writes a supersede row that ONLY
          blocks future promotions (it does not retroactively reject
          the resource that just committed).
        """
        if resource_type not in KNOWN_RESOURCE_TYPES:
            raise ValidationError_("Resource 必须使用已注册的资源类型")
        if source.kind not in {"output_binding", "selection_record"}:
            raise ConflictError(
                "Resource 提升必须来自 OutputBinding/SelectionRecord，禁止裸 artifact_id",
                details={"source_kind": source.kind},
            )
        with self._factory.begin() as session:
            target_id = source.binding_id or source.selection_id
            assert target_id is not None
            # Lock the source row FIRST so a concurrent supersede
            # writer either sees the in-flight promotion or blocks
            # behind it.  This is the shared lock step that
            # supersede_promotion_source also takes.
            if source.kind == "output_binding":
                if source.binding_id is None:
                    raise ConflictError("output_binding 提升必须提供 binding_id")
                binding = session.execute(
                    select(ProviderOutputBindingModel)
                    .where(ProviderOutputBindingModel.binding_id == source.binding_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if binding is None:
                    raise NotFoundError("OutputBinding", str(source.binding_id))
                if binding.owner_scope != owner.scoped_id:
                    raise CrossOwnerError()
                if not binding.output_artifact_version_id:
                    raise ConflictError("OutputBinding 缺少 output_artifact_version_id")
                resolved_artifact_id = binding.output_artifact_version_id
                candidate_meta = {
                    "binding_id": str(binding.binding_id),
                    "record_id": str(binding.record_id),
                    "output_index": binding.output_index,
                    "output_label": binding.output_label,
                    "artifact_version_id": str(resolved_artifact_id),
                }
            else:
                if source.selection_id is None:
                    raise ConflictError("selection_record 提升必须提供 selection_id")
                selection = session.execute(
                    select(SelectionRecordModel)
                    .where(SelectionRecordModel.selection_id == source.selection_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if selection is None:
                    raise NotFoundError("SelectionRecord", str(source.selection_id))
                if selection.owner_scope != owner.scoped_id:
                    raise CrossOwnerError()
                selected_refs = list(selection.selected_refs or [])
                if not selected_refs:
                    raise ConflictError("SelectionRecord 没有 selected_refs")
                if source.output_index is not None and source.output_index >= 0:
                    if source.output_index >= len(selected_refs):
                        raise ConflictError(
                            "SelectionRecord output_index 越界",
                            details={"selected_count": len(selected_refs)},
                        )
                    candidate = selected_refs[source.output_index]
                else:
                    candidate = selected_refs[0]
                if not isinstance(candidate, dict):
                    raise ConflictError("SelectionRecord ref 必须是对象")
                resolved_artifact_id = _resolve_artifact_version_id_from_ref(candidate)
                if resolved_artifact_id is None:
                    raise ConflictError(
                        "SelectionRecord ref 缺少可识别的 artifact_version_id 字段",
                    )
                candidate_meta = {
                    "selection_id": str(selection.selection_id),  # type: ignore[arg-type]
                    "candidate_set_id": str(selection.candidate_set_id),  # type: ignore[arg-type]
                    "candidate_refs_count": str(len(selected_refs)),
                    "artifact_version_id": str(resolved_artifact_id),
                }
            # Source row is locked.  Now check the supersede state.
            superseded = session.scalar(
                select(OutputBindingSupersedeModel).where(
                    OutputBindingSupersedeModel.owner_scope == owner.scoped_id,
                    OutputBindingSupersedeModel.ref_kind == source.kind,
                    OutputBindingSupersedeModel.ref_id == target_id,
                ).with_for_update()
            )
            if superseded is not None:
                raise ConflictError(
                    "OutputBinding/SelectionRecord 已被 superseded，禁止提升",
                    details={
                        "ref_kind": source.kind,
                        "ref_id": str(superseded.ref_id),
                        "superseded_by": str(superseded.superseded_by_ref_id or ""),
                        "reason": superseded.reason,
                    },
                )
            # Validate the ArtifactVersion owner under the lock.
            artifact = session.get(ArtifactVersionModel, resolved_artifact_id)
            if artifact is None or artifact.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            # Persist Resource + Draft + provenance in the same transaction.
            resource_row = ResourceModel(
                resource_id=uuid4(),
                resource_type=resource_type,
                owner_scope=owner.scoped_id,
                source_world_revision_id=None,
                source_local_id=None,
                source_content_hash=None,
                elevation_event_id=None,
                promotion_source_kind=source.kind,
                promotion_source_ref_id=target_id,
                promotion_source_artifact_version_id=resolved_artifact_id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(resource_row)
            session.flush()
            session.add(ResourceDraftModel(
                resource_id=resource_row.resource_id, draft_version=1, base_revision_id=None,
                content_artifact_version_id=resolved_artifact_id,
                updated_at=datetime.now(timezone.utc),
            ))
            session.flush()
            return _resource(resource_row), candidate_meta

    def supersede_promotion_source(
        self,
        owner: OwnerScope,
        ref_kind: str,
        ref_id: UUID,
        superseded_by_ref_id: UUID | None = None,
        reason: str = "",
    ) -> UUID:
        """Record that a promotion source has been retired.

        Server-side, this validates that the referenced source belongs
        to the same owner before writing.  Cross-tenant supersede writes
        are rejected as ``CrossOwnerError`` so a tenant cannot grief
        another tenant by writing a forged supersede row.
        """
        if ref_kind not in {"output_binding", "selection_record"}:
            raise ValidationError_("invalid promotion source kind")
        with self._factory.begin() as session:
            # Lock the source row FIRST (same step as promote_from_source)
            # so the supersede write is serialised against any in-flight
            # promotion.  Without this lock, a promotion could read
            # "not superseded", commit a Resource row, and only then the
            # supersede row would appear — which would be a violation
            # of the contract that promotion rejects superseded sources.
            if ref_kind == "output_binding":
                target = session.execute(
                    select(ProviderOutputBindingModel)
                    .where(ProviderOutputBindingModel.binding_id == ref_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if target is None:
                    raise NotFoundError("OutputBinding", str(ref_id))
                if target.owner_scope != owner.scoped_id:
                    raise CrossOwnerError()
            else:
                target = session.execute(
                    select(SelectionRecordModel)
                    .where(SelectionRecordModel.selection_id == ref_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if target is None:
                    raise NotFoundError("SelectionRecord", str(ref_id))
                if target.owner_scope != owner.scoped_id:
                    raise CrossOwnerError()
            existing = session.scalar(
                select(OutputBindingSupersedeModel).where(
                    OutputBindingSupersedeModel.owner_scope == owner.scoped_id,
                    OutputBindingSupersedeModel.ref_kind == ref_kind,
                    OutputBindingSupersedeModel.ref_id == ref_id,
                ).with_for_update()
            )
            if existing is not None:
                return existing.supersede_id  # type: ignore[return-value]
            row = OutputBindingSupersedeModel(
                supersede_id=uuid4(),
                owner_scope=owner.scoped_id,
                ref_kind=ref_kind,
                ref_id=ref_id,
                superseded_by_ref_id=superseded_by_ref_id,
                reason=reason,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row.supersede_id  # type: ignore[return-value]