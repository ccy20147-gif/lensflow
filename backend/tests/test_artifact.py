"""TF-WF-005: Contract tests for Artifact & Resource services.

Tests cover:
  - ArtifactVersion creation (immutable, lineage tracking)
  - Resource/Draft/Revision CRUD with CAS
  - Cross-owner boundary enforcement
  - Stale propagation
  - Lineage query
"""
from __future__ import annotations

import pytest
from uuid import uuid4

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    NotFoundError,
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
from src.schemas.enums import RevisionStatus
from src.domain.artifact.artifact_service import ArtifactService
from src.domain.artifact.resource_service import ResourceService


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def owner() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def other_owner() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def artifact_svc() -> ArtifactService:
    return ArtifactService()


@pytest.fixture
def resource_svc(artifact_svc: ArtifactService) -> ResourceService:
    return ResourceService(artifact_service=artifact_svc)


@pytest.fixture
def sample_av(artifact_svc: ArtifactService, owner: OwnerScope) -> ArtifactVersion:
    return artifact_svc.create_artifact_version(
        schema_id="toonflow.image.v1",
        schema_version=1,
        owner_scope=owner,
        content_json={"width": 1024, "height": 768, "format": "png"},
    )


# ------------------------------------------------------------------
# Test: ArtifactVersion
# ------------------------------------------------------------------


class TestArtifactService:
    def test_create_artifact_version(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        av = artifact_svc.create_artifact_version(
            schema_id="test.v1",
            schema_version=1,
            owner_scope=owner,
            content_json={"key": "value"},
        )
        assert isinstance(av, ArtifactVersion)
        assert av.artifact_version_id is not None
        assert av.content_hash is not None
        assert len(av.content_hash) == 64  # SHA-256

    def test_get_artifact_version(
        self, artifact_svc: ArtifactService, sample_av: ArtifactVersion
    ) -> None:
        fetched = artifact_svc.get_artifact_version(sample_av.artifact_version_id)
        assert fetched.artifact_version_id == sample_av.artifact_version_id
        assert fetched.schema_id == "toonflow.image.v1"

    def test_get_artifact_version_not_found(
        self, artifact_svc: ArtifactService
    ) -> None:
        with pytest.raises(NotFoundError):
            artifact_svc.get_artifact_version(uuid4())

    def test_artifact_version_immutable_different_ids(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        """AC-1: Same content produces different version IDs (immutable)."""
        av1 = artifact_svc.create_artifact_version(
            schema_id="test.v1",
            schema_version=1,
            owner_scope=owner,
            content_json={"data": 1},
        )
        av2 = artifact_svc.create_artifact_version(
            schema_id="test.v1",
            schema_version=1,
            owner_scope=owner,
            content_json={"data": 1},
        )
        assert av1.artifact_version_id != av2.artifact_version_id
        # But content hashes may be the same
        assert av1.content_hash == av2.content_hash

    def test_lineage_tracking(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        """FR-7: Lineage records input refs."""
        input_av = artifact_svc.create_artifact_version(
            schema_id="test.v1",
            schema_version=1,
            owner_scope=owner,
            content_json={"input": True},
        )
        input_ref = ArtifactRef(
            artifact_id=input_av.artifact_id,
            artifact_version_id=input_av.artifact_version_id,
            schema_id="test.v1",
            schema_version=1,
        )
        output_av = artifact_svc.create_artifact_version(
            schema_id="test.v2",
            schema_version=1,
            owner_scope=owner,
            content_json={"output": True},
            lineage_input_refs=[input_ref],
        )
        assert len(output_av.lineage_input_refs) == 1
        assert output_av.lineage_input_refs[0].artifact_version_id == input_av.artifact_version_id

    def test_get_lineage(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        input_av = artifact_svc.create_artifact_version(
            schema_id="test.v1",
            schema_version=1,
            owner_scope=owner,
            content_json={"input": True},
        )
        input_ref = ArtifactRef(
            artifact_id=input_av.artifact_id,
            artifact_version_id=input_av.artifact_version_id,
            schema_id="test.v1",
            schema_version=1,
        )
        output_av = artifact_svc.create_artifact_version(
            schema_id="test.v2",
            schema_version=1,
            owner_scope=owner,
            content_json={"output": True},
            lineage_input_refs=[input_ref],
        )
        lineage = artifact_svc.get_lineage(output_av.artifact_version_id)
        assert lineage["version"]["artifact_version_id"] == str(output_av.artifact_version_id)
        assert len(lineage["inputs"]) == 1

    def test_stale_downstream(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        """FR-9: Find downstream artifacts that consume a given version."""
        input_av = artifact_svc.create_artifact_version(
            schema_id="test.v1", schema_version=1, owner_scope=owner,
            content_json={"input": True},
        )
        input_ref = ArtifactRef(
            artifact_id=input_av.artifact_id,
            artifact_version_id=input_av.artifact_version_id,
            schema_id="test.v1",
            schema_version=1,
        )
        output_av = artifact_svc.create_artifact_version(
            schema_id="test.v2", schema_version=1, owner_scope=owner,
            content_json={"output": True},
            lineage_input_refs=[input_ref],
        )
        stale = artifact_svc.find_stale_downstream(input_av.artifact_version_id)
        assert len(stale) == 1
        assert stale[0].artifact_version_id == output_av.artifact_version_id

    def test_cross_owner_ref_rejected(
        self, artifact_svc: ArtifactService, owner: OwnerScope, other_owner: OwnerScope
    ) -> None:
        """AC-4: Cross-owner ArtifactRef rejected."""
        av = artifact_svc.create_artifact_version(
            schema_id="test.v1", schema_version=1, owner_scope=owner,
            content_json={"secret": True},
        )
        with pytest.raises(CrossOwnerError):
            artifact_svc.get_artifact_ref(av.artifact_id, av.artifact_version_id, other_owner)

    def test_same_owner_ref_allowed(
        self, artifact_svc: ArtifactService, owner: OwnerScope
    ) -> None:
        av = artifact_svc.create_artifact_version(
            schema_id="test.v1", schema_version=1, owner_scope=owner,
            content_json={"data": 1},
        )
        ref = artifact_svc.get_artifact_ref(av.artifact_id, av.artifact_version_id, owner)
        assert ref.artifact_id == av.artifact_id
        assert ref.artifact_version_id == av.artifact_version_id


# ------------------------------------------------------------------
# Test: Resource Service
# ------------------------------------------------------------------


class TestResourceService:
    def test_create_resource(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(resource_type="character", owner_scope=owner)
        assert isinstance(res, Resource)
        assert res.resource_id is not None
        assert res.resource_type == "character"

    def test_get_resource(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        fetched = resource_svc.get_resource(res.resource_id)
        assert fetched.resource_id == res.resource_id

    def test_get_draft(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        assert isinstance(draft, ResourceDraft)
        assert draft.resource_id == res.resource_id

    def test_save_draft_cas(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        """FR-8: CAS on draft_version."""
        res = resource_svc.create_resource(owner_scope=owner)
        current_draft = resource_svc.get_draft(res.resource_id)

        new_av_id = uuid4()
        saved = resource_svc.save_draft(
            resource_id=res.resource_id,
            content_artifact_version_id=new_av_id,
            base_draft_version=current_draft.draft_version,
        )
        assert saved.draft_version == current_draft.draft_version + 1
        assert saved.content_artifact_version_id == new_av_id

    def test_save_draft_cas_conflict(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        with pytest.raises(ConflictError):
            resource_svc.save_draft(
                resource_id=res.resource_id,
                content_artifact_version_id=uuid4(),
                base_draft_version=999,  # Wrong version
            )

    def test_freeze_revision(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        """AC-2: Freeze creates immutable revision with content ref."""
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)

        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)
        assert isinstance(rev, ResourceRevision)
        assert rev.revision_number == 1
        assert rev.revision_status == RevisionStatus.ACTIVE

    def test_freeze_revision_cas_conflict(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        with pytest.raises(ConflictError):
            resource_svc.freeze_revision(res.resource_id, 999)

    def test_retire_revision(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)
        retired = resource_svc.retire_revision(rev.revision_id)
        assert retired.revision_status == RevisionStatus.RETIRED

    def test_list_revisions(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        resource_svc.freeze_revision(res.resource_id, draft.draft_version)

        # Update draft and freeze again
        new_draft = resource_svc.save_draft(
            resource_id=res.resource_id,
            content_artifact_version_id=uuid4(),
            base_draft_version=draft.draft_version,
        )
        resource_svc.freeze_revision(res.resource_id, new_draft.draft_version)

        revs = resource_svc.list_revisions(res.resource_id)
        assert len(revs) == 2

    def test_get_active_revision(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)
        active = resource_svc.get_active_revision(res.resource_id)
        assert active is not None
        assert active.revision_id == rev.revision_id

    # ------------------------------------------------------------------
    # Cross-owner tests
    # ------------------------------------------------------------------

    def test_cross_owner_resource_ref_without_grant_rejected(
        self, resource_svc: ResourceService, owner: OwnerScope, other_owner: OwnerScope
    ) -> None:
        """AC-4: Cross-owner ResourceRef without grant_snapshot_id rejected."""
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)

        with pytest.raises(CrossOwnerError):
            resource_svc.resolve_resource_ref(
                resource_id=res.resource_id,
                revision_id=rev.revision_id,
                requesting_scope=other_owner,
                grant_snapshot_id=None,
            )

    def test_cross_owner_resource_ref_with_grant_allowed(
        self, resource_svc: ResourceService, owner: OwnerScope, other_owner: OwnerScope
    ) -> None:
        """AC-4: Cross-owner with valid grant_snapshot_id is allowed."""
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)

        ref = resource_svc.resolve_resource_ref(
            resource_id=res.resource_id,
            revision_id=rev.revision_id,
            requesting_scope=other_owner,
            grant_snapshot_id=uuid4(),
        )
        assert isinstance(ref, ResourceRef)
        assert ref.resource_id == res.resource_id
        assert ref.revision_id == rev.revision_id
        assert ref.grant_snapshot_id is not None

    def test_same_owner_resource_ref_no_grant_needed(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)

        ref = resource_svc.resolve_resource_ref(
            resource_id=res.resource_id,
            revision_id=rev.revision_id,
            requesting_scope=owner,
            grant_snapshot_id=None,
        )
        assert isinstance(ref, ResourceRef)
        assert ref.resource_id == res.resource_id

    # ------------------------------------------------------------------
    # Stale propagation
    # ------------------------------------------------------------------

    def test_find_stale_drafts(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        """FR-9: Drafts based on old revision are marked stale."""
        res = resource_svc.create_resource(owner_scope=owner)
        draft = resource_svc.get_draft(res.resource_id)
        rev = resource_svc.freeze_revision(res.resource_id, draft.draft_version)

        # New revision exists; any draft not based on the latest is stale
        stale = resource_svc.find_stale_drafts(res.resource_id, rev.revision_id)
        assert len(stale) == 0  # Current draft IS based on latest

    def test_resource_lifecycle(
        self, resource_svc: ResourceService, owner: OwnerScope
    ) -> None:
        """Complete lifecycle: create → edit draft → freeze → retrieve → retire."""
        res = resource_svc.create_resource(
            resource_type="character", owner_scope=owner
        )
        assert res.resource_id is not None

        # Edit draft
        draft1 = resource_svc.get_draft(res.resource_id)
        av_id = uuid4()
        draft2 = resource_svc.save_draft(
            resource_id=res.resource_id,
            content_artifact_version_id=av_id,
            base_draft_version=draft1.draft_version,
        )
        assert draft2.draft_version == 1  # started at 0, now 1

        # Freeze revision
        rev1 = resource_svc.freeze_revision(res.resource_id, draft2.draft_version)
        assert rev1.revision_number == 1

        # Edit again
        draft3 = resource_svc.get_draft(res.resource_id)
        draft4 = resource_svc.save_draft(
            resource_id=res.resource_id,
            content_artifact_version_id=uuid4(),
            base_draft_version=draft3.draft_version,
        )
        rev2 = resource_svc.freeze_revision(res.resource_id, draft4.draft_version)
        assert rev2.revision_number == 2

        # List revisions
        revs = resource_svc.list_revisions(res.resource_id)
        assert len(revs) == 2

        # Retire first revision
        resource_svc.retire_revision(rev1.revision_id)
        assert resource_svc.get_revision(rev1.revision_id).revision_status == RevisionStatus.RETIRED

        # Active is rev2
        active = resource_svc.get_active_revision(res.resource_id)
        assert active is not None
        assert active.revision_id == rev2.revision_id
