"""TF-PLT-002: PostgreSQL-backed Project repository."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError
from src.infra.db.models import ProjectModel
from src.infra.db.session import get_session_factory
from src.schemas.enums import ProjectStatus
from src.schemas.models import OwnerScope


class SqlProjectRepository:
    """Persistent Project CRUD with owner-scope enforcement."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_project(
        self,
        owner_scope: OwnerScope,
        name: str,
        description: str = "",
    ) -> ProjectModel:
        """Create a project under the given owner."""
        if not name:
            raise ConflictError(message="项目名称不能为空")
        scope_id = owner_scope.scoped_id
        with self._factory.begin() as session:
            row = ProjectModel(
                project_id=uuid.uuid4(),
                owner_scope=scope_id,
                name=name,
                description=description,
                status=ProjectStatus.ACTIVE,
                default_entry="canvas",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    def get_project(self, project_id: uuid.UUID, owner_scope: OwnerScope) -> ProjectModel:
        """Load a project and enforce owner-scope.  Raises CrossOwnerError on mismatch."""
        scope_id = owner_scope.scoped_id
        with self._factory() as session:
            row = session.scalar(
                select(ProjectModel).where(
                    ProjectModel.project_id == project_id,
                    ProjectModel.owner_scope == scope_id,
                )
            )
            if row is None:
                # Determine whether this is a not-found or a cross-owner case
                any_row = session.get(ProjectModel, project_id)
                if any_row is not None and any_row.owner_scope != scope_id:
                    raise CrossOwnerError()
                raise NotFoundError("Project", str(project_id))
            return row

    def list_projects(
        self,
        owner_scope: OwnerScope,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ProjectModel]:
        scope_id = owner_scope.scoped_id
        with self._factory() as session:
            stmt = (
                select(ProjectModel)
                .where(ProjectModel.owner_scope == scope_id)
                .order_by(ProjectModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.scalars(stmt))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def archive(self, project_id: uuid.UUID, owner_scope: OwnerScope) -> ProjectModel:
        return self._transition(project_id, owner_scope, ProjectStatus.ARCHIVED)

    def restore(self, project_id: uuid.UUID, owner_scope: OwnerScope) -> ProjectModel:
        return self._transition(project_id, owner_scope, ProjectStatus.ACTIVE)

    def mark_deletion_pending(self, project_id: uuid.UUID, owner_scope: OwnerScope) -> ProjectModel:
        return self._transition(project_id, owner_scope, ProjectStatus.DELETION_PENDING)

    def hard_delete(self, project_id: uuid.UUID, owner_scope: OwnerScope) -> None:
        """Hard delete; blocks while project is still ACTIVE."""
        with self._factory.begin() as session:
            row = session.scalar(
                select(ProjectModel).where(
                    ProjectModel.project_id == project_id,
                    ProjectModel.owner_scope == owner_scope.scoped_id,
                )
            )
            if row is None:
                raise CrossOwnerError()
            if row.status == ProjectStatus.ACTIVE:
                raise ConflictError(message="请先归档或进入删除中状态再硬删除")
            session.execute(
                update(ProjectModel).where(ProjectModel.project_id == project_id)
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _transition(
        self, project_id: uuid.UUID, owner_scope: OwnerScope, new_status: ProjectStatus
    ) -> ProjectModel:
        with self._factory.begin() as session:
            row = session.scalar(
                select(ProjectModel).where(
                    ProjectModel.project_id == project_id,
                    ProjectModel.owner_scope == owner_scope.scoped_id,
                )
            )
            if row is None:
                raise CrossOwnerError()
            row.status = new_status
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row