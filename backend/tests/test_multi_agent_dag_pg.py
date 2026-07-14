"""AGT-004 acceptance: fixed Agent revisions execute as a typed DAG."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.models import NodeRunAttemptModel, NodeRunModel, WorkflowModel, WorkflowRevisionModel
from src.infra.db.session import get_session_factory
from src.infra.db.identity_repository import get_session_store
from src.schemas.enums import NodeRunStatus, RevisionStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


class _AtlasResponses:
    def __init__(self) -> None:
        self.count = 0

    def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
        self.count += 1
        request = httpx.Request(method, url)
        return httpx.Response(200, request=request, json={"data": [{"text": f"agent-{self.count}"}], "model_version": "test"})


def _agent(repo: SqlAgentRepository, owner: OwnerScope, name: str):
    definition = repo.create_definition(name=name, description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(definition.agent_id, {
        "output_schema_ref": "toonflow.agent_output.v1",
        "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "sop_steps": [{"step_id": "write", "instruction": "Return typed text"}],
        "execution_policy": {"provider_ref": "atlascloud/test", "max_attempts": 1},
    })
    return repo.promote_revision(revision.revision_id)


def test_three_fixed_agents_pass_only_pinned_artifacts_downstream() -> None:
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    owner = OwnerScope(kind="user", id=uuid4())
    agents = SqlAgentRepository(factory)
    world, outline, expand = (_agent(agents, owner, value) for value in ("world", "outline", "expand"))
    workflow_id, revision_id = uuid4(), uuid4()
    graph = {
        "nodes": [
            {"id": "world", "type": f"agent.invoke.{world.revision_id}", "config": {"agent_revision_id": str(world.revision_id)}},
            {"id": "outline", "type": f"agent.invoke.{outline.revision_id}", "config": {"agent_revision_id": str(outline.revision_id)}},
            {"id": "expand", "type": f"agent.invoke.{expand.revision_id}", "config": {"agent_revision_id": str(expand.revision_id)}},
        ],
        "edges": [{"source": "world", "target": "outline"}, {"source": "outline", "target": "expand"}],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph=graph, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc)))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=revision_id, registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, plan_hash="three-agent"), owner_scope=owner)
    runtime.start_run(run.run_id)
    transport = _AtlasResponses()
    invoke = AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=transport, api_key="test", base_url="https://atlas.test"))
    worker = RuntimeWorker(factory, agent_invocations=invoke)

    previous_output: str | None = None
    for node_id, revision in (("world", world), ("outline", outline), ("expand", expand)):
        with factory() as session:
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == node_id))
            assert node is not None
            attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
            assert attempt is not None
            assert str((attempt.fixed_input or {}).get("agent_revision_id")) == str(revision.revision_id)
            refs = (attempt.fixed_input or {}).get("upstream_artifact_refs", [])
            if previous_output is None:
                assert refs == []
            else:
                assert refs and refs[0]["artifact_version_ids"] == [previous_output]
        claim = worker.claim_next_attempt("agent-worker", run_id=run.run_id)
        assert claim is not None and claim.attempt.attempt_id == attempt.attempt_id
        result = worker.execute_attempt(attempt.attempt_id)
        assert result["status"] == "completed" and result["agent_revision_id"] == str(revision.revision_id)
        previous_output = result["artifact_version_ids"][0]

    with factory() as session:
        statuses = {row.node_instance_id: row.status for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))}
        assert statuses == {"world": NodeRunStatus.COMPLETED, "outline": NodeRunStatus.COMPLETED, "expand": NodeRunStatus.COMPLETED}
    # The trace is owner-scoped HTTP data rather than a UI-side reconstruction.
    from src.app import app
    token = get_session_store().issue(owner.id)["token"]
    with TestClient(app) as client:
        trace = client.get(f"/api/v1/runtime/workflow-runs/{run.run_id}", headers={"Authorization": f"Bearer {token}"})
    assert trace.status_code == 200
    outline_trace = next(item for item in trace.json()["nodes"] if item["node_instance_id"] == "outline")
    assert outline_trace["attempts"][0]["fixed_input"]["agent_revision_id"] == str(outline.revision_id)
    assert outline_trace["attempts"][0]["output_artifact_version_ids"]


def test_failed_agent_keeps_failure_trace_and_only_declared_fallback_is_scheduled() -> None:
    factory = get_session_factory()
    owner = OwnerScope(kind="user", id=uuid4())
    workflow_id, revision_id = uuid4(), uuid4()
    graph = {
        "nodes": [
            {"id": "primary", "type": "agent_invoke", "config": {"failure_policy": "configured_fallback", "fallback_node_id": "fallback"}},
            {"id": "fallback", "type": "agent_invoke", "config": {}},
            {"id": "unrelated", "type": "agent_invoke", "config": {}},
        ],
        "edges": [{"source": "primary", "target": "fallback"}],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1, graph_hash="fallback", execution_hash="e", registry_snapshot_id=uuid4(), graph=graph, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc)))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=revision_id, registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, plan_hash="fallback"), owner_scope=owner)
    with factory() as session:
        primary = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "primary"))
        assert primary is not None
        primary_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == primary.node_run_id))
        assert primary_attempt is not None
    runtime.fail_attempt(primary_attempt.attempt_id)
    with factory() as session:
        primary = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "primary"))
        fallback = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "fallback"))
        unrelated = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "unrelated"))
        assert primary is not None and primary.status == NodeRunStatus.FAILED
        assert fallback is not None and fallback.status == NodeRunStatus.READY
        fallback_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == fallback.node_run_id))
        assert fallback_attempt is not None and fallback_attempt.fixed_input["fallback_for_node_ids"] == ["primary"]
        # The unrelated root remains independently executable.
        assert unrelated is not None and unrelated.status == NodeRunStatus.READY
