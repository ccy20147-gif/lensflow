"""Project Service — project lifecycle, archive/restore, deletion."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError
from src.schemas.enums import ProjectStatus
from src.schemas.models import OwnerScope, Project


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class ProjectRecord:
    """Internal project storage record."""

    def __init__(
        self,
        project_id: str,
        owner_scope: OwnerScope,
        name: str,
        description: str = "",
        status: ProjectStatus = ProjectStatus.ACTIVE,
        default_entry: str = "",
        workflow_ids: list[str] | None = None,
        resource_ids: list[str] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.project_id = project_id
        self.owner_scope = owner_scope
        self.name = name
        self.description = description
        self.status = status
        self.default_entry = default_entry
        self.workflow_ids = workflow_ids or []
        self.resource_ids = resource_ids or []
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)


class ProjectStore(Protocol):
    """Interface for project persistence."""

    def save(self, record: ProjectRecord) -> None: ...
    def get_by_id(self, project_id: str) -> ProjectRecord | None: ...
    def list_by_owner(self, owner_scope: OwnerScope) -> list[ProjectRecord]: ...
    def delete(self, project_id: str) -> None: ...


class InMemoryProjectStore:
    """Thread-safe in-memory project store."""

    def __init__(self) -> None:
        self._projects: dict[str, ProjectRecord] = {}

    def save(self, record: ProjectRecord) -> None:
        self._projects[record.project_id] = record

    def get_by_id(self, project_id: str) -> ProjectRecord | None:
        return self._projects.get(project_id)

    def list_by_owner(self, owner_scope: OwnerScope) -> list[ProjectRecord]:
        key = owner_scope.scoped_id
        return [p for p in self._projects.values() if p.owner_scope.scoped_id == key]

    def delete(self, project_id: str) -> None:
        self._projects.pop(project_id, None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProjectService:
    """Manages project CRUD, archive/restore, and resource library."""

    def __init__(self, store: ProjectStore | None = None) -> None:
        self._store = store or InMemoryProjectStore()

    def _to_project(self, rec: ProjectRecord) -> Project:
        return Project(
            project_id=uuid.UUID(rec.project_id),
            owner_scope=rec.owner_scope,
            name=rec.name,
            description=rec.description,
            status=rec.status,
            default_entry=rec.default_entry,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    def _get_record(self, project_id: str) -> ProjectRecord:
        rec = self._store.get_by_id(project_id)
        if rec is None:
            raise NotFoundError("Project", project_id)
        return rec

    # ---- CRUD (FR-1) ----

    def create_project(
        self,
        owner_scope: OwnerScope,
        name: str,
        description: str = "",
    ) -> Project:
        """Create a new project."""
        project_id = str(uuid.uuid4())
        now = _now()
        record = ProjectRecord(
            project_id=project_id,
            owner_scope=owner_scope,
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self._store.save(record)
        return self._to_project(record)

    def get_project(self, project_id: str, caller_owner: OwnerScope) -> Project:
        """Get project by ID with owner validation (cross-owner blocked)."""
        rec = self._get_record(project_id)
        # Validate owner access (FR-5)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        return self._to_project(rec)

    def list_projects(self, caller_owner: OwnerScope) -> list[Project]:
        """List all projects for a given owner scope."""
        records = self._store.list_by_owner(caller_owner)
        return [self._to_project(r) for r in records]

    def update_project(
        self,
        project_id: str,
        caller_owner: OwnerScope,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        """Update project metadata."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if rec.status not in (ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED):
            raise ConflictError("项目状态不允许修改")

        if name is not None:
            rec.name = name
        if description is not None:
            rec.description = description
        rec.updated_at = _now()
        self._store.save(rec)
        return self._to_project(rec)

    # ---- Archive / Restore (FR-9) ----

    def archive_project(self, project_id: str, caller_owner: OwnerScope) -> Project:
        """Archive a project — makes it read-only."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if rec.status != ProjectStatus.ACTIVE:
            raise ConflictError("只有活跃项目可以归档")

        rec.status = ProjectStatus.ARCHIVED
        rec.updated_at = _now()
        self._store.save(rec)
        return self._to_project(rec)

    def restore_project(self, project_id: str, caller_owner: OwnerScope) -> Project:
        """Restore an archived project back to active."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if rec.status != ProjectStatus.ARCHIVED:
            raise ConflictError("只有已归档项目可以恢复")

        rec.status = ProjectStatus.ACTIVE
        rec.updated_at = _now()
        self._store.save(rec)
        return self._to_project(rec)

    # ---- Deletion flow (FR-10) ----

    def request_deletion(self, project_id: str, caller_owner: OwnerScope) -> Project:
        """Initiate project deletion flow."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if rec.status in (ProjectStatus.DELETION_PENDING, ProjectStatus.DELETED_TOMBSTONE):
            raise ConflictError("删除请求已存在或项目已删除")

        rec.status = ProjectStatus.DELETION_PENDING
        rec.updated_at = _now()
        self._store.save(rec)
        return self._to_project(rec)

    def confirm_deletion(self, project_id: str, caller_owner: OwnerScope) -> None:
        """Finalize deletion — marks as tombstone."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if rec.status != ProjectStatus.DELETION_PENDING:
            raise ConflictError("项目未处于待删除状态")

        rec.status = ProjectStatus.DELETED_TOMBSTONE
        rec.updated_at = _now()
        rec.name = f"[deleted_{rec.project_id[:8]}]"
        self._store.save(rec)

    # ---- Workflow association (FR-2) ----

    def add_workflow(self, project_id: str, workflow_id: str, caller_owner: OwnerScope) -> None:
        """Associate a workflow with a project."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if workflow_id not in rec.workflow_ids:
            rec.workflow_ids.append(workflow_id)
            rec.updated_at = _now()
            self._store.save(rec)

    def list_workflows(self, project_id: str, caller_owner: OwnerScope) -> list[str]:
        """List workflow IDs associated with a project."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        return list(rec.workflow_ids)

    # ---- Resource association (FR-2) ----

    def add_resource(self, project_id: str, resource_id: str, caller_owner: OwnerScope) -> None:
        """Associate a resource with a project."""
        rec = self._get_record(project_id)
        if rec.owner_scope.scoped_id != caller_owner.scoped_id:
            raise CrossOwnerError()
        if resource_id not in rec.resource_ids:
            rec.resource_ids.append(resource_id)
            rec.updated_at = _now()
            self._store.save(rec)
