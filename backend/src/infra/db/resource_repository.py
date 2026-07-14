"""Canonical PostgreSQL resource and entitlement repository (TF-WF-005)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactVersionModel,
    OutboxEventModel,
    ResourceDraftModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import OwnerScope, Resource, ResourceDraft, ResourceRef, ResourceRevision


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _world_character_content(world_content: object, local_id: str) -> dict[str, object]:
    """Resolve a WorldPackage embedded character by stable local ID.

    A source ID is evidence only when it is present in the immutable content
    that the referenced WorldRevision fixed.  Do not accept a UI-provided ID
    without this lookup: that would create a forged lineage edge.
    """
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


def _owner(raw: str) -> OwnerScope:
    kind, _, value = raw.partition(":")
    return OwnerScope(kind=kind, id=UUID(value))


def _resource(row: ResourceModel) -> Resource:
    return Resource(
        resource_id=row.resource_id,
        resource_type=row.resource_type,
        owner_scope=_owner(row.owner_scope),
        source_world_revision_id=row.source_world_revision_id,
        source_local_id=row.source_local_id,
        source_content_hash=row.source_content_hash,
        elevation_event_id=row.elevation_event_id,
        created_at=row.created_at,
    )


def _draft(row: ResourceDraftModel) -> ResourceDraft:
    return ResourceDraft(resource_id=row.resource_id, draft_version=row.draft_version, base_revision_id=row.base_revision_id, content_artifact_version_id=row.content_artifact_version_id, updated_at=row.updated_at)


def _revision(row: ResourceRevisionModel) -> ResourceRevision:
    return ResourceRevision(
        resource_id=row.resource_id,
        revision_id=row.revision_id,
        revision_number=row.revision_number,
        content_artifact_version_id=row.content_artifact_version_id,
        revision_status=row.revision_status,
        created_from_artifact_version_id=row.created_from_artifact_version_id,
        source_world_revision_id=row.source_world_revision_id,
        source_local_id=row.source_local_id,
        source_content_hash=row.source_content_hash,
        elevation_event_id=row.elevation_event_id,
        created_at=row.created_at,
    )


class SqlResourceRepository:
    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

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
        with self._factory.begin() as session:
            artifact = session.get(ArtifactVersionModel, content_artifact_version_id)
            if artifact is None or artifact.owner_scope != owner.scoped_id:
                raise NotFoundError("ArtifactVersion", str(content_artifact_version_id))
            source_content_hash: str | None = None
            has_origin = any((source_world_revision_id, source_local_id, elevation_event_id))
            if has_origin:
                if resource_type != "character":
                    raise ConflictError("仅 Character Resource 可以声明 World OC 提升来源")
                if not all((source_world_revision_id, source_local_id)):
                    raise ConflictError("OC 提升必须同时固定 source_world_revision_id 和 source_local_id")
                # Fence concurrent promotions of the same immutable World
                # revision.  The duplicate lookup below then sees the first
                # committed elevation instead of leaking an IntegrityError.
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
                # Promotion copies the selected immutable embedded record; a
                # caller may not attach arbitrary Character content to a
                # valid World revision/local-id pair.
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
            row = ResourceModel(
                resource_id=uuid4(), resource_type=resource_type, owner_scope=owner.scoped_id,
                source_world_revision_id=source_world_revision_id, source_local_id=source_local_id,
                source_content_hash=source_content_hash,
                elevation_event_id=elevation_event_id, created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            session.add(ResourceDraftModel(resource_id=row.resource_id, draft_version=1, base_revision_id=None, content_artifact_version_id=content_artifact_version_id, updated_at=datetime.now(timezone.utc)))
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
        """List canonical resource identities owned by ``owner``.

        This intentionally reads ResourceModel rather than a UI projection so
        a dropped search/index projection can always be reconstructed.
        """
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
            rows = session.scalars(
                select(ResourceRevisionModel)
                .where(ResourceRevisionModel.resource_id == resource_id)
                .order_by(ResourceRevisionModel.revision_number.desc())
            )
            return [_revision(row) for row in rows]

    def provenance(self, resource_id: UUID, owner: OwnerScope) -> dict[str, object]:
        """Return an explainable canonical revision chain, never a mutable latest.

        Blob placement and metadata come from the referenced immutable
        ArtifactVersion row.  No caller can rewrite them through this API.
        """
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
                # Referential integrity prevents this in normal operation; keep
                # the response diagnosable rather than silently inventing data.
                return {"artifact_version_id": str(version_id), "availability": "missing"}
            return {
                "artifact_version_id": str(row.artifact_version_id),
                "schema_id": row.schema_id,
                "schema_version": row.schema_version,
                "content_hash": row.content_hash,
                "content_uri": row.content_uri,
                "blob_uri": row.blob_uri,
                "created_by_run_id": str(row.created_by_run_id) if row.created_by_run_id else None,
                "lineage_input_refs": row.lineage_input_refs or [],
                "metadata": row.metadata_json or {},
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
        """Rebuild the Resource Library view from canonical PostgreSQL rows.

        There is deliberately no persisted ``latest`` cache to repair.  The
        returned rows are the deterministic projection consumed by the UI and
        are useful as an operational rebuild/checksum exercise.
        """
        resources = self.list_resources(owner)
        return {"source": "canonical_postgresql", "resources": [self.provenance(row.resource_id, owner) for row in resources]}

    def save_draft(self, resource_id: UUID, owner: OwnerScope, content_artifact_version_id: UUID, base_draft_version: int) -> ResourceDraft:
        self.get(resource_id, owner)
        with self._factory.begin() as session:
            artifact = session.get(ArtifactVersionModel, content_artifact_version_id)
            if artifact is None or artifact.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            stmt = update(ResourceDraftModel).where(ResourceDraftModel.resource_id == resource_id, ResourceDraftModel.draft_version == base_draft_version).values(draft_version=ResourceDraftModel.draft_version + 1, content_artifact_version_id=content_artifact_version_id, updated_at=datetime.now(timezone.utc))
            if session.execute(stmt).rowcount != 1:
                raise ConflictError("ResourceDraft compare-and-swap conflict")
            row = session.get(ResourceDraftModel, resource_id)
            assert row is not None
            return _draft(row)

    def freeze(self, resource_id: UUID, owner: OwnerScope, base_draft_version: int) -> ResourceRevision:
        self.get(resource_id, owner)
        with self._factory.begin() as session:
            draft = session.execute(select(ResourceDraftModel).where(ResourceDraftModel.resource_id == resource_id).with_for_update()).scalar_one_or_none()
            if draft is None:
                raise NotFoundError("ResourceDraft", str(resource_id))
            if draft.draft_version != base_draft_version:
                raise ConflictError("ResourceDraft compare-and-swap conflict")
            last = session.scalar(select(ResourceRevisionModel.revision_number).where(ResourceRevisionModel.resource_id == resource_id).order_by(ResourceRevisionModel.revision_number.desc()).limit(1)) or 0
            session.execute(update(ResourceRevisionModel).where(ResourceRevisionModel.resource_id == resource_id, ResourceRevisionModel.revision_status == RevisionStatus.ACTIVE).values(revision_status=RevisionStatus.RETIRED))
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
            session.flush()
            return _revision(row)

    _CAPABILITY_ACTIONS = frozenset({"reference", "execute", "redistribute"})

    @classmethod
    def _normalize_actions(cls, actions: list[str]) -> list[str]:
        normalized = sorted({str(action).strip().lower() for action in actions if str(action).strip()})
        if not normalized or any(action not in cls._CAPABILITY_ACTIONS for action in normalized):
            raise ValidationError_("Resource grant contains an unsupported capability action")
        return normalized

    def grant(self, revision_id: UUID, owner: OwnerScope, grantee: OwnerScope, *, capability_actions: list[str]) -> UUID:
        actions = self._normalize_actions(capability_actions)
        with self._factory.begin() as session:
            revision = session.get(ResourceRevisionModel, revision_id)
            if revision is None:
                raise NotFoundError("ResourceRevision", str(revision_id))
            resource = session.get(ResourceModel, revision.resource_id)
            if resource is None or resource.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            row = ResourceGrantSnapshotModel(grant_snapshot_id=uuid4(), resource_revision_id=revision_id,
                grantee_scope=grantee.scoped_id, capability_actions=actions, status="active", created_at=datetime.now(timezone.utc))
            session.add(row)
            session.flush()
            return row.grant_snapshot_id

    def revoke_grant(self, revision_id: UUID, grant_snapshot_id: UUID, owner: OwnerScope) -> None:
        """Revoke a fixed grant without rewriting historical plans or traces."""
        with self._factory.begin() as session:
            revision = session.get(ResourceRevisionModel, revision_id)
            resource = session.get(ResourceModel, revision.resource_id) if revision else None
            grant = session.get(ResourceGrantSnapshotModel, grant_snapshot_id)
            if resource is None or resource.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            if grant is None or grant.resource_revision_id != revision_id:
                raise NotFoundError("ResourceGrantSnapshot", str(grant_snapshot_id))
            if grant.status != "revoked":
                grant.status = "revoked"
                grant.revoked_at = datetime.now(timezone.utc)
                session.add(OutboxEventModel(
                    event_id=uuid4(), aggregate_type="resource_grant",
                    aggregate_id=grant.grant_snapshot_id, event_type="resource_grant.revoked",
                    payload={"resource_revision_id": str(revision_id), "grantee_scope": grant.grantee_scope},
                    purpose="resource_grant_revoke", created_at=datetime.now(timezone.utc),
                ))

    def resolve_ref(self, resource_id: UUID, revision_id: UUID, requester: OwnerScope,
                    grant_snapshot_id: UUID | None, *, required_actions: set[str] | None = None) -> ResourceRef:
        with self._factory() as session:
            resource = session.get(ResourceModel, resource_id)
            revision = session.get(ResourceRevisionModel, revision_id)
            if resource is None or revision is None or revision.resource_id != resource_id or revision.revision_status != RevisionStatus.ACTIVE:
                raise NotFoundError("ResourceRevision", str(revision_id))
            if resource.owner_scope != requester.scoped_id:
                if grant_snapshot_id is None:
                    raise CrossOwnerError()
                grant = session.get(ResourceGrantSnapshotModel, grant_snapshot_id)
                if grant is None or grant.resource_revision_id != revision_id or grant.grantee_scope != requester.scoped_id or grant.status != "active":
                    raise CrossOwnerError()
                actions = set(grant.capability_actions or [])
                if not set(required_actions or ()).issubset(actions):
                    raise CrossOwnerError()
            return ResourceRef(resource_id=resource_id, resource_type=resource.resource_type, revision_id=revision_id, grant_snapshot_id=grant_snapshot_id)
