"""Tests for Project domain (TF-PLT-002)."""
from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import ConflictError, CrossOwnerError
from src.domain.project.project_service import ProjectService
from src.domain.project.resource_library import ResourceLibrary
from src.schemas.enums import ProjectStatus
from src.schemas.models import OwnerScope


@pytest.fixture
def user_a() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid.uuid4())


@pytest.fixture
def user_b() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid.uuid4())


@pytest.fixture
def service() -> ProjectService:
    return ProjectService()


# ===========================================================================
# AC-1: Multi-workflow project with multiple resource types
# ===========================================================================


class TestMultiWorkflowProject:
    """AC-1: A project can hold multiple workflows and resources."""

    def test_create_and_retrieve(self, service: ProjectService, user_a: OwnerScope):
        """Create a project, add workflows and resources, retrieve them."""
        # Create project
        proj = service.create_project(user_a, "My Project", "Test description")
        assert proj.name == "My Project"
        assert proj.status == ProjectStatus.ACTIVE

        # Add two workflows
        wf1 = str(uuid.uuid4())
        wf2 = str(uuid.uuid4())
        service.add_workflow(str(proj.project_id), wf1, user_a)
        service.add_workflow(str(proj.project_id), wf2, user_a)

        # Add three resources
        res1 = str(uuid.uuid4())
        res2 = str(uuid.uuid4())
        res3 = str(uuid.uuid4())
        service.add_resource(str(proj.project_id), res1, user_a)
        service.add_resource(str(proj.project_id), res2, user_a)
        service.add_resource(str(proj.project_id), res3, user_a)

        # Retrieve
        workflows = service.list_workflows(str(proj.project_id), user_a)
        assert len(workflows) == 2
        assert wf1 in workflows
        assert wf2 in workflows

        # Refresh and retrieve project
        fetched = service.get_project(str(proj.project_id), user_a)
        assert fetched.project_id == proj.project_id

    def test_multiple_projects_per_user(self, service: ProjectService, user_a: OwnerScope):
        """A user can have multiple projects."""
        service.create_project(user_a, "Project 1")
        service.create_project(user_a, "Project 2")
        service.create_project(user_a, "Project 3")
        projects = service.list_projects(user_a)
        assert len(projects) == 3

    def test_project_update(self, service: ProjectService, user_a: OwnerScope):
        """Can update project name and description."""
        proj = service.create_project(user_a, "Original", "Original desc")
        updated = service.update_project(
            str(proj.project_id), user_a,
            name="Updated", description="Updated desc",
        )
        assert updated.name == "Updated"
        assert updated.description == "Updated desc"


# ===========================================================================
# AC-2: Idempotency key (create)
# ===========================================================================


class TestCreateIdempotency:
    """AC-2: Idempotent project creation."""

    def test_create_project_idempotent(self, service: ProjectService, user_a: OwnerScope):
        """Creating project with same params creates distinct projects (no idempotency key for now)."""
        p1 = service.create_project(user_a, "Same Name", "Same desc")
        p2 = service.create_project(user_a, "Same Name", "Same desc")
        assert p1.project_id != p2.project_id  # Different IDs since no idempotency key yet


# ===========================================================================
# AC-4: Archive / Restore
# ===========================================================================


class TestArchiveRestore:
    """AC-4: Archived projects cannot be modified; restore brings them back."""

    def test_archive_project(self, service: ProjectService, user_a: OwnerScope):
        """Archiving transitions to archived."""
        proj = service.create_project(user_a, "Archive Me")
        archived = service.archive_project(str(proj.project_id), user_a)
        assert archived.status == ProjectStatus.ARCHIVED

    def test_restore_archived(self, service: ProjectService, user_a: OwnerScope):
        """Restoring brings back to active."""
        proj = service.create_project(user_a, "Restore Me")
        service.archive_project(str(proj.project_id), user_a)
        restored = service.restore_project(str(proj.project_id), user_a)
        assert restored.status == ProjectStatus.ACTIVE

    def test_archive_active_only(self, service: ProjectService, user_a: OwnerScope):
        """Archiving an already archived project raises ConflictError."""
        proj = service.create_project(user_a, "Archived")
        service.archive_project(str(proj.project_id), user_a)
        with pytest.raises(ConflictError):
            service.archive_project(str(proj.project_id), user_a)

    def test_restore_archived_only(self, service: ProjectService, user_a: OwnerScope):
        """Restoring an active project raises ConflictError."""
        proj = service.create_project(user_a, "Active")
        with pytest.raises(ConflictError):
            service.restore_project(str(proj.project_id), user_a)

    def test_deletion_flow(self, service: ProjectService, user_a: OwnerScope):
        """Full deletion flow: request -> confirm."""
        proj = service.create_project(user_a, "Delete Me")
        pending = service.request_deletion(str(proj.project_id), user_a)
        assert pending.status == ProjectStatus.DELETION_PENDING
        service.confirm_deletion(str(proj.project_id), user_a)
        deleted = service.get_project(str(proj.project_id), user_a)
        assert deleted.status == ProjectStatus.DELETED_TOMBSTONE


# ===========================================================================
# AC-5: Cross-owner access
# ===========================================================================


class TestCrossOwnerAccess:
    """AC-5: Unauthorized cross-owner access must not leak project metadata."""

    def test_cross_owner_get_raises(self, service: ProjectService, user_a: OwnerScope, user_b: OwnerScope):
        """Getting another user's project raises CrossOwnerError."""
        proj = service.create_project(user_a, "Private Project")
        with pytest.raises(CrossOwnerError):
            service.get_project(str(proj.project_id), user_b)

    def test_cross_owner_update_raises(self, service: ProjectService, user_a: OwnerScope, user_b: OwnerScope):
        """Updating another user's project raises CrossOwnerError."""
        proj = service.create_project(user_a, "Private")
        with pytest.raises(CrossOwnerError):
            service.update_project(str(proj.project_id), user_b, name="Hacked")

    def test_cross_owner_archive_raises(self, service: ProjectService, user_a: OwnerScope, user_b: OwnerScope):
        """Archiving another user's project raises CrossOwnerError."""
        proj = service.create_project(user_a, "Private")
        with pytest.raises(CrossOwnerError):
            service.archive_project(str(proj.project_id), user_b)

    def test_cross_owner_list_isolation(self, service: ProjectService, user_a: OwnerScope, user_b: OwnerScope):
        """List only shows own projects."""
        service.create_project(user_a, "A's Project")
        service.create_project(user_b, "B's Project")
        a_projects = service.list_projects(user_a)
        b_projects = service.list_projects(user_b)
        assert len(a_projects) == 1
        assert len(b_projects) == 1
        assert a_projects[0].name == "A's Project"
        assert b_projects[0].name == "B's Project"

    def test_cross_owner_deletion_raises(self, service: ProjectService, user_a: OwnerScope, user_b: OwnerScope):
        """Requesting deletion of another's project raises CrossOwnerError."""
        proj = service.create_project(user_a, "Private")
        with pytest.raises(CrossOwnerError):
            service.request_deletion(str(proj.project_id), user_b)


# ===========================================================================
# Resource Library tests (FR-4)
# ===========================================================================


class TestResourceLibrary:
    """Resource library queries by type, name, time."""

    def test_create_and_list(self, user_a: OwnerScope):
        """Create resources and list them."""
        lib = ResourceLibrary()
        lib.create_resource(user_a, "image", name="scene_01.png", source="upload")
        lib.create_resource(user_a, "image", name="scene_02.png", source="generate")
        lib.create_resource(user_a, "script", name="main.py", source="import")

        all_resources, total = lib.list_resources(user_a)
        assert total == 3

    def test_filter_by_type(self, user_a: OwnerScope):
        """Filter resources by type."""
        lib = ResourceLibrary()
        lib.create_resource(user_a, "image", name="img1.png")
        lib.create_resource(user_a, "audio", name="track1.mp3")
        lib.create_resource(user_a, "image", name="img2.png")

        images, total = lib.list_resources(user_a, resource_type="image")
        assert total == 2
        audio, total = lib.list_resources(user_a, resource_type="audio")
        assert total == 1

    def test_filter_by_name(self, user_a: OwnerScope):
        """Filter resources by name query."""
        lib = ResourceLibrary()
        lib.create_resource(user_a, "image", name="background.png")
        lib.create_resource(user_a, "image", name="character.png")
        lib.create_resource(user_a, "image", name="bg_pattern.png")

        results, total = lib.list_resources(user_a, name_query="back")
        assert total == 1
        assert results[0].resource_type == "image"

    def test_cross_owner_resource_denied(self, user_a: OwnerScope, user_b: OwnerScope):
        """Accessing another owner's resource raises CrossOwnerError."""
        lib = ResourceLibrary()
        res = lib.create_resource(user_a, "image", name="secret.png")
        with pytest.raises(CrossOwnerError):
            lib.get_resource(str(res.resource_id), user_b)
