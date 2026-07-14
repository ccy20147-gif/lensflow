"""PostgreSQL integration coverage for the durable workflow lifecycle.

Run against the local ToonFlow PostgreSQL after ``alembic upgrade head``.
Each test owns a UUID-scoped workflow and removes it at teardown.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.session import get_session_factory
from src.schemas.models import OwnerScope


@pytest.fixture
def sql_service() -> SqlWorkflowService:
    if os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1":
        pytest.skip("set TOONFLOW_RUN_PG_TESTS=1 to run PostgreSQL integration tests")
    factory = get_session_factory()
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - local infrastructure guard
        pytest.skip(f"PostgreSQL integration database unavailable: {exc}")
    return SqlWorkflowService(factory)


def test_workflow_persists_across_service_instances(sql_service: SqlWorkflowService) -> None:
    workflow_id = uuid4()
    owner = OwnerScope(kind="user", id=uuid4())
    created = False
    try:
        workflow = sql_service.create_workflow(workflow_id=workflow_id, owner_scope=owner)
        created = True
        initial = sql_service.get_draft(workflow.workflow_id)
        graph = {"nodes": {"source": {"type": "input"}}, "edges": []}
        saved = sql_service.save_draft(
            workflow.workflow_id,
            graph=graph,
            config={"mode": "integration"},
            layout={"source": {"x": 20, "y": 40}},
            base_graph_hash=initial.graph_hash,
        )

        # A new service instance must observe committed state, not process memory.
        reloaded = SqlWorkflowService(get_session_factory()).get_draft(workflow.workflow_id)
        assert reloaded.draft_version == saved.draft_version
        assert reloaded.graph == graph
        assert reloaded.graph_hash == saved.graph_hash

        revision = sql_service.create_revision_from_draft(workflow.workflow_id, uuid4())
        assert SqlWorkflowService(get_session_factory()).get_revision_graph(revision.revision_id) == graph
    finally:
        if created:
            sql_service.delete_workflow(workflow_id)
