"""TF-WF-005: Resource Service

Manages Resource/Draft/Revision CRUD with compare-and-swap (CAS),
cross-owner boundary enforcement, and stale propagation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    NotFoundError,
    ValidationError_,
)
from src.schemas.enums import RevisionStatus
from src.schemas.models import (
    ArtifactRef,
    ArtifactVersion,
    OwnerScope,
    Resource,
    ResourceDraft,
    ResourceRef,
    ResourceRevision,
)


class ResourceService:
    """In-memory resource store for Foundation stage."""

    def __init__(self, artifact_service: Any = None) -> None:
        # resource_id -> Resource
        self._resources: dict[UUID, Resource] = {}
        # resource_id -> ResourceDraft | None
        self._drafts: dict[UUID, ResourceDraft] = {}
        # resource_id -> list[(revision_number, ResourceRevision)]
        self._revisions: dict[UUID, list[ResourceRevision]] = {}
        # revision_id -> ResourceRevision
        self._revision_index: dict[UUID, ResourceRevision] = {}
        # resource_id -> active revision id
        self._active_revision: dict[UUID, UUID | None] = {}
        # resource_id -> resource_type
        self._resource_types: dict[str, dict[str, Any]] = {}
        self._artifact_service = artifact_service

    # ------------------------------------------------------------------
    # Resource CRUD
    # ------------------------------------------------------------------

    def create_resource(
        self,
        resource_id: UUID | None = None,
        resource_type: str = "generic",
        owner_scope: OwnerScope | None = None,
        initial_content_av: ArtifactVersion | None = None,
    ) -> Resource:
        """Create a new Resource with an initial draft."""
        res_id = resource_id or uuid4()
        res = Resource(
            resource_id=res_id,
            resource_type=resource_type,
            owner_scope=owner_scope,  # type: ignore[arg-type]
            created_at=datetime.now(timezone.utc),
        )
        self._resources[res_id] = res

        # Create initial draft
        draft = ResourceDraft(
            resource_id=res_id,
            draft_version=0,
            base_revision_id=None,
            content_artifact_version_id=initial_content_av.artifact_version_id
            if initial_content_av
            else uuid4(),
            updated_at=datetime.now(timezone.utc),
        )
        self._drafts[res_id] = draft
        self._revisions[res_id] = []
        self._active_revision[res_id] = None
        return res

    def get_resource(self, resource_id: UUID) -> Resource:
        res = self._resources.get(resource_id)
        if res is None:
            raise NotFoundError("Resource", str(resource_id))
        return res

    def list_resources(
        self,
        owner_scope: OwnerScope | None = None,
        resource_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Resource]:
        results = list(self._resources.values())
        if owner_scope is not None:
            results = [r for r in results if r.owner_scope == owner_scope]
        if resource_type is not None:
            results = [r for r in results if r.resource_type == resource_type]
        return results[offset : offset + limit]

    def delete_resource(self, resource_id: UUID) -> None:
        if resource_id not in self._resources:
            raise NotFoundError("Resource", str(resource_id))
        del self._resources[resource_id]
        self._drafts.pop(resource_id, None)
        for rev in self._revisions.pop(resource_id, []):
            self._revision_index.pop(rev.revision_id, None)
        self._active_revision.pop(resource_id, None)

    # ------------------------------------------------------------------
    # ResourceDraft operations
    # ------------------------------------------------------------------

    def get_draft(self, resource_id: UUID) -> ResourceDraft:
        self.get_resource(resource_id)
        draft = self._drafts.get(resource_id)
        if draft is None:
            raise NotFoundError("ResourceDraft", str(resource_id))
        return draft

    def save_draft(
        self,
        resource_id: UUID,
        content_artifact_version_id: UUID,
        base_draft_version: int,
    ) -> ResourceDraft:
        """Save draft with compare-and-swap on draft_version.

        Raises:
            ConflictError: If base_draft_version doesn't match current.
        """
        current_draft = self.get_draft(resource_id)

        if current_draft.draft_version != base_draft_version:
            raise ConflictError(
                message=(
                    f"ResourceDraft {resource_id} 冲突: "
                    f"base_draft_version {base_draft_version} 不匹配当前 {current_draft.draft_version}"
                )
            )

        new_draft = ResourceDraft(
            resource_id=resource_id,
            draft_version=base_draft_version + 1,
            base_revision_id=current_draft.base_revision_id,
            content_artifact_version_id=content_artifact_version_id,
            updated_at=datetime.now(timezone.utc),
        )
        self._drafts[resource_id] = new_draft
        return new_draft

    # ------------------------------------------------------------------
    # ResourceRevision operations
    # ------------------------------------------------------------------

    def freeze_revision(
        self, resource_id: UUID, base_draft_version: int
    ) -> ResourceRevision:
        """Freeze the current draft into an immutable ResourceRevision.

        Uses compare-and-swap on draft_version to prevent conflicts.
        """
        draft = self.get_draft(resource_id)

        if draft.draft_version != base_draft_version:
            raise ConflictError(
                message=(
                    f"Resource {resource_id} 冻结冲突: "
                    f"draft_version {base_draft_version} 不匹配当前 {draft.draft_version}"
                )
            )

        revisions = self._revisions.setdefault(resource_id, [])
        rev_number = (revisions[-1].revision_number + 1) if revisions else 1

        # Find the previous active revision's content for lineage
        prev_revision_id = None
        if revisions:
            prev_revision_id = revisions[-1].revision_id

        rev = ResourceRevision(
            resource_id=resource_id,
            revision_id=uuid4(),
            revision_number=rev_number,
            content_artifact_version_id=draft.content_artifact_version_id,
            revision_status=RevisionStatus.ACTIVE,
            created_from_artifact_version_id=prev_revision_id,
            created_at=datetime.now(timezone.utc),
        )
        self._revision_index[rev.revision_id] = rev
        revisions.append(rev)
        self._active_revision[resource_id] = rev.revision_id

        # Update draft's base_revision_id
        self._drafts[resource_id] = ResourceDraft(
            resource_id=resource_id,
            draft_version=draft.draft_version,
            base_revision_id=rev.revision_id,
            content_artifact_version_id=draft.content_artifact_version_id,
            updated_at=draft.updated_at,
        )

        return rev

    def get_revision(self, revision_id: UUID) -> ResourceRevision:
        rev = self._revision_index.get(revision_id)
        if rev is None:
            raise NotFoundError("ResourceRevision", str(revision_id))
        return rev

    def get_active_revision(self, resource_id: UUID) -> ResourceRevision | None:
        active_id = self._active_revision.get(resource_id)
        if active_id is None:
            return None
        return self._revision_index.get(active_id)

    def list_revisions(
        self, resource_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[ResourceRevision]:
        revisions = self._revisions.get(resource_id, [])
        return revisions[-limit - offset :] if limit else revisions[offset:]

    def retire_revision(self, revision_id: UUID) -> ResourceRevision:
        rev = self.get_revision(revision_id)
        rev.revision_status = RevisionStatus.RETIRED
        if self._active_revision.get(rev.resource_id) == revision_id:
            self._active_revision[rev.resource_id] = None
        return rev

    # ------------------------------------------------------------------
    # ResourceRef (cross-owner resolution)
    # ------------------------------------------------------------------

    def resolve_resource_ref(
        self,
        resource_id: UUID,
        revision_id: UUID,
        requesting_scope: OwnerScope,
        grant_snapshot_id: UUID | None = None,
    ) -> ResourceRef:
        """Resolve a ResourceRef with cross-owner boundary check.

        Cross-owner access requires:
          1. A valid grant_snapshot_id (Foundation: must be non-None for cross-owner).
          2. The requesting_scope is different from the resource's owner_scope.

        Same-owner access: grant_snapshot_id may be None.

        Raises:
            NotFoundError: If resource or revision doesn't exist.
            CrossOwnerError: If cross-owner access without grant_snapshot_id.
        """
        res = self.get_resource(resource_id)
        rev = self.get_revision(revision_id)

        if rev.resource_id != resource_id:
            raise NotFoundError("ResourceRevision", f"{resource_id}/{revision_id}")

        # Cross-owner check
        if (
            requesting_scope is not None
            and res.owner_scope is not None
            and res.owner_scope != requesting_scope
        ):
            if grant_snapshot_id is None:
                raise CrossOwnerError()

        return ResourceRef(
            resource_id=resource_id,
            resource_type=res.resource_type,
            revision_id=revision_id,
            role=None,
            grant_snapshot_id=grant_snapshot_id,
        )

    # ------------------------------------------------------------------
    # Stale propagation
    # ------------------------------------------------------------------

    def find_stale_drafts(
        self, resource_id: UUID, upstream_revision_id: UUID
    ) -> list[ResourceDraft]:
        """Find drafts that are based on an old revision and are now stale.

        In Foundation, any draft whose base_revision_id is older than the
        given upstream revision is considered stale.
        """
        drafts: list[ResourceDraft] = []
        draft = self._drafts.get(resource_id)
        if draft and draft.base_revision_id != upstream_revision_id:
            drafts.append(draft)
        return drafts
