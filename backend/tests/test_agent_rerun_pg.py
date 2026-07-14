"""AGT-004: owner-scoped fixed Agent reruns are durable and auditable."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from src.api.routes.workflow import _agent_definitions_for_graph
from src.app import app
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline
from src.domain.workflow.compiler import WorkflowCompiler
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.identity_repository import get_session_store
from src.infra.db.models import (
    ArtifactVersionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    OutboxEventModel,
)
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.session import get_session_factory
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1",
)


class _AtlasResponses:
    """Deterministic AtlasCloud transport: every call returns a new artifact body."""

    def __init__(self) -> None:
        self.calls = 0

    def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
        self.calls += 1
        request = httpx.Request(method, url)
        return httpx.Response(
            200, request=request,
            json={"data": [{"text": f"rerun-output-{self.calls}"}], "model_version": "atlas-test"},
        )


def _headers(owner_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_session_store().issue(owner_id)['token']}"}


def _published_agent(repo: SqlAgentRepository, owner: OwnerScope):
    definition = repo.create_definition(
        name=f"rerunnable-{uuid4()}", description="", agent_kind="configurable", owner_scope=owner.scoped_id,
    )
    revision = repo.create_revision(definition.agent_id, {
        # Identical ports make the two fixed revisions a valid typed chain.
        "input_schema_ref": "toonflow.story.v1",
        "output_schema_ref": "toonflow.story.v1",
        "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "sop_steps": [{"step_id": "produce", "instruction": "Return a typed text result"}],
        "execution_policy": {"provider_ref": "atlascloud/test", "max_attempts": 1},
    })
    return repo.promote_revision(revision.revision_id)


def _node(node_id: str, revision_id: UUID) -> dict[str, object]:
    node_type = f"agent.invoke.{revision_id}"
    return {
        "id": node_id, "type": node_type,
        "data": {"node_type_id": node_type, "config": {"agent_revision_id": str(revision_id)}},
    }


def _publish_fixed_agent_run(owner: OwnerScope, *, include_unfixed: bool = False):
    """Use the durable service lifecycle, including a frozen RegistrySnapshot."""
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    ensure_public_business_node_baseline(SqlRegistryService(factory))
    agents = SqlAgentRepository(factory)
    agent = _published_agent(agents, owner)
    nodes: list[dict[str, object]] = [_node("writer", agent.revision_id), _node("downstream", agent.revision_id)]
    if include_unfixed:
        nodes.append({"id": "unfixed", "type": "agent_invoke", "config": {}})
    graph = {
        "nodes": nodes,
        "edges": [{"id": "writer-downstream", "source": "writer", "target": "downstream", "sourceHandle": "output", "targetHandle": "input"}],
    }
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(workflow.workflow_id, graph, {}, {}, draft.graph_hash, [str(agent.revision_id)])
    registry = SqlRegistryService(factory)
    snapshot, _row = registry.create_snapshot(_agent_definitions_for_graph(graph, owner))
    revision, plan = workflows.publish_compiled_revision(workflow.workflow_id, snapshot, WorkflowCompiler())
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=owner, input_snapshot={"prompt": "immutable source input"})
    runtime.start_run(run.run_id)
    return factory, revision, run, agent


def _execute_agent(worker: RuntimeWorker, factory, run_id: UUID, node_instance_id: str) -> UUID:
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == run_id, NodeRunModel.node_instance_id == node_instance_id,
        ))
        assert node is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert attempt is not None
    claim = worker.claim_next_attempt(f"rerun-worker-{uuid4()}", run_id=run_id)
    assert claim is not None and claim.attempt.attempt_id == attempt.attempt_id
    result = worker.execute_attempt(attempt.attempt_id)
    assert result["status"] == "completed"
    return UUID(result["artifact_version_ids"][0])


def test_fixed_agent_rerun_derives_new_output_and_audits_stale_downstream() -> None:
    owner = OwnerScope(kind="user", id=uuid4())
    factory, revision, source, _agent = _publish_fixed_agent_run(owner)
    transport = _AtlasResponses()
    worker = RuntimeWorker(factory, agent_invocations=AgentInvocationService(
        factory, adapter=AtlasCloudAdapter(transport=transport, api_key="test", base_url="https://atlas.test"),
    ))

    original_output = _execute_agent(worker, factory, source.run_id, "writer")
    # Finish the declared consumer as well: the stale list is about a completed
    # source branch, not merely a draft edge.
    _execute_agent(worker, factory, source.run_id, "downstream")

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/runtime/workflow-runs/{source.run_id}/agents/rerun",
            headers=_headers(owner.id), json={"node_instance_id": "writer"},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    derived_run_id = UUID(body["run_id"])
    assert body["source_run_id"] == str(source.run_id)
    assert body["rerun_node_id"] == "writer"
    assert body["stale_downstream_node_ids"] == ["downstream"]
    assert body["fixed_input_snapshot"]["partial_run"]["source_run_id"] == str(source.run_id)

    rerun_output = _execute_agent(worker, factory, derived_run_id, "writer")
    assert rerun_output != original_output
    with factory() as session:
        assert session.get(ArtifactVersionModel, original_output) is not None
        assert session.get(ArtifactVersionModel, rerun_output) is not None
        event = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == derived_run_id,
            OutboxEventModel.event_type == "agent.rerun.created",
        ))
        assert event is not None
        assert event.payload == {
            "source_run_id": str(source.run_id), "rerun_node_id": "writer",
            "stale_downstream_node_ids": ["downstream"],
        }
        derived_nodes = list(session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == derived_run_id)))
        assert [item.node_instance_id for item in derived_nodes] == ["writer"]
    assert revision.revision_id == source.workflow_revision_id


def test_agent_rerun_rejects_cross_owner_unfixed_and_non_agent_nodes() -> None:
    owner = OwnerScope(kind="user", id=uuid4())
    _factory, _revision, source, _agent = _publish_fixed_agent_run(owner, include_unfixed=True)
    other = uuid4()
    with TestClient(app) as client:
        cross_owner = client.post(
            f"/api/v1/runtime/workflow-runs/{source.run_id}/agents/rerun",
            headers=_headers(other), json={"node_instance_id": "writer"},
        )
        generic_agent = client.post(
            f"/api/v1/runtime/workflow-runs/{source.run_id}/agents/rerun",
            headers=_headers(owner.id), json={"node_instance_id": "unfixed"},
        )
        missing = client.post(
            f"/api/v1/runtime/workflow-runs/{source.run_id}/agents/rerun",
            headers=_headers(owner.id), json={"node_instance_id": "not-a-node"},
        )
    assert cross_owner.status_code == 404
    assert generic_agent.status_code == 422
    assert missing.status_code == 422
