"""AGT-004 owner-scoped Workbench Agent trace read model."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.app import app
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.agent.request_input import AgentRequestInputService
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.identity_repository import get_session_store
from src.infra.db.models import NodeRunAttemptModel, NodeRunModel, WorkflowModel, WorkflowRevisionModel
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


class _Atlas:
    def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request(method, url), json={"data": [{"text": "trace"}], "model_version": "atlas-test"})


def _headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_session_store().issue(user_id)['token']}"}


def test_agent_trace_is_owner_scoped_and_recovers_request_input_state() -> None:
    factory = get_session_factory()
    owner = OwnerScope(kind="user", id=uuid4())
    repo = SqlAgentRepository(factory)
    definition = repo.create_definition(name=f"trace-{uuid4()}", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    agent = repo.promote_revision(repo.create_revision(definition.agent_id, {
        "output_schema_ref": "toonflow.story.v1", "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "sop_steps": [{"step_id": "write", "instruction": "Return text"}], "execution_policy": {"provider_ref": "atlascloud/test"},
    }).revision_id)
    workflow_id, revision_id = uuid4(), uuid4()
    graph = {"nodes": [{"id": "agent", "type": f"agent.invoke.{agent.revision_id}", "config": {"agent_revision_id": str(agent.revision_id)}}], "edges": []}
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph=graph, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc)))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=revision_id, registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, plan_hash="trace"), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert attempt is not None
    runtime.set_attempt_running(attempt.attempt_id, "trace-worker")
    worker = RuntimeWorker(factory, agent_invocations=AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=_Atlas(), api_key="test", base_url="https://atlas.test")))
    worker.execute_attempt(attempt.attempt_id)
    # A later RequestInput is a durable recovery state, not a client-only modal.
    task = AgentRequestInputService(factory).create(agent_revision_id=agent.revision_id, run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id,
        schema_ref="story_input.v1", question="Need a story choice", timeout_minutes=5, idempotency_token=str(uuid4()), input_schema={"type": "object", "properties": {"choice": {"type": "string"}}}, requester_scope=owner.scoped_id)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/runtime/workflow-runs/{run.run_id}/agent-trace", headers=_headers(owner.id))
        denied = client.get(f"/api/v1/runtime/workflow-runs/{run.run_id}/agent-trace", headers=_headers(uuid4()))
    assert response.status_code == 200
    item = response.json()["agents"][0]["attempts"][0]
    assert item["agent_revision_id"] == str(agent.revision_id)
    assert item["output_artifact_version_ids"]
    assert any(trace["phase"] == "completed" for trace in item["sop_trace"])
    assert any(trace["sop_steps"] == [{"step_id": "write", "status": "planned"}] for trace in item["sop_trace"])
    assert item["request_input"]["task_id"] == str(task.task_id)
    assert item["request_input"]["status"] == "waiting"
    assert item["request_input_answered"] is False
    assert denied.status_code == 404
