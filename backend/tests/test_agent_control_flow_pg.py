"""AGT-004: fixed Agents remain executable inside durable control-flow paths."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from src.api.routes.workflow import _agent_definitions_for_graph, _assert_graph_reference_authorization
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.domain.workflow.compiler import WorkflowCompiler
from src.core.exceptions import ForbiddenError
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.models import ArtifactVersionModel, NodeRunAttemptModel, NodeRunModel, WorkflowModel, WorkflowRevisionModel
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, RevisionStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


class _Atlas:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, request=httpx.Request(method, url), json={"data": [{"text": f"agent-{self.calls}"}], "model_version": "test"})


def _agent(owner: OwnerScope, name: str):
    repo = SqlAgentRepository(get_session_factory())
    definition = repo.create_definition(name=f"{name}-{uuid4()}", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(definition.agent_id, {
        "input_schema_ref": "toonflow.story.v1", "output_schema_ref": "toonflow.story.v1",
        "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "sop_steps": [{"step_id": "write", "instruction": "Return typed text"}],
        "execution_policy": {"provider_ref": "atlascloud/test"},
    })
    return repo.promote_revision(revision.revision_id)


def _run(owner: OwnerScope, graph: dict, *, inputs: dict | None = None) -> tuple[RuntimeService, UUID]:
    factory = get_session_factory()
    workflow_id, revision_id = uuid4(), uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph=graph, config={}, layout={},
            revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc)))
    runtime = RuntimeService(factory)
    plan = CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, plan_hash=str(uuid4()))
    run = runtime.create_run(compiled_plan=plan, owner_scope=owner, input_snapshot=inputs or {})
    runtime.start_run(run.run_id)
    return runtime, run.run_id


def _worker() -> tuple[RuntimeWorker, _Atlas]:
    atlas = _Atlas()
    factory = get_session_factory()
    return RuntimeWorker(factory, agent_invocations=AgentInvocationService(
        factory, adapter=AtlasCloudAdapter(transport=atlas, api_key="test", base_url="https://atlas.test"),
    )), atlas


def _attempt(run_id: UUID, node_id: str) -> NodeRunAttemptModel:
    with get_session_factory()() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run_id, NodeRunModel.node_instance_id == node_id))
        assert node is not None
        item = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert item is not None
        session.expunge(item)
        return item


def test_parallel_agents_join_stably_despite_inverted_completion() -> None:
    owner = OwnerScope(kind="user", id=uuid4())
    left, right = _agent(owner, "left"), _agent(owner, "right")
    graph = {
        "nodes": [
            {"id": "left", "type": f"agent.invoke.{left.revision_id}", "config": {"agent_revision_id": str(left.revision_id)}},
            {"id": "right", "type": f"agent.invoke.{right.revision_id}", "config": {"agent_revision_id": str(right.revision_id)}},
            {"id": "join", "type": "join", "config": {"strategy": "all"}},
        ],
        "edges": [{"source": "left", "target": "join"}, {"source": "right", "target": "join"}],
    }
    runtime, run_id = _run(owner, graph)
    worker, _atlas = _worker()
    # Deliberately finish right before left. Each was independently READY.
    for node_id in ("right", "left"):
        attempt = _attempt(run_id, node_id)
        runtime.set_attempt_running(attempt.attempt_id, f"lease-{node_id}")
        result = worker.execute_attempt(attempt.attempt_id)
        assert result["status"] == "completed"
    join = _attempt(run_id, "join")
    claim = worker.claim_next_attempt("join-worker", run_id=run_id)
    assert claim is not None and claim.attempt.attempt_id == join.attempt_id
    result = worker.execute_attempt(join.attempt_id)
    assert result["kind"] == "join"
    with get_session_factory()() as session:
        saved = session.get(ArtifactVersionModel, UUID(result["artifact_version_ids"][0]))
        assert saved is not None
        assert [item["source_node_id"] for item in saved.content_json["source_outputs"]] == ["left", "right"]


@pytest.mark.parametrize("kind", ["map", "ordered_map", "fold"])
def test_agent_map_variants_execute_real_fixed_agent_attempts(kind: str) -> None:
    owner = OwnerScope(kind="user", id=uuid4())
    agent = _agent(owner, kind)
    graph = {"nodes": [{"id": "iterate", "type": kind, "config": {
        "items": [{"prompt": "one"}, {"prompt": "two"}], "max_items": 2, "max_concurrency": 2,
        "agent_revision_id": str(agent.revision_id),
    }}], "edges": []}
    _runtime, run_id = _run(owner, graph)
    worker, atlas = _worker()
    while True:
        item = worker.claim_next_map_item(f"{kind}-worker", run_id=run_id)
        if item is None:
            break
        attempt = _attempt(run_id, f"iterate[{item.item_index}]")
        result = worker.execute_attempt(attempt.attempt_id)
        assert result["kind"] == "agent_invoke" and result["status"] == "completed"
    assert atlas.calls == 2
    with get_session_factory()() as session:
        attempts = list(session.scalars(select(NodeRunAttemptModel).join(NodeRunModel).where(
            NodeRunModel.run_id == run_id, NodeRunModel.node_instance_id.like("iterate[%"),
        )))
        assert len(attempts) == 2
        assert all(item.status == AttemptStatus.COMPLETED for item in attempts)
        assert all(item.fixed_input["agent_revision_id"] == str(agent.revision_id) for item in attempts)
        assert all(item.fixed_input.get("map_output") for item in attempts)


def test_cross_owner_resource_ref_is_checked_at_compile_and_again_at_agent_execution() -> None:
    source, consumer = OwnerScope(kind="user", id=uuid4()), OwnerScope(kind="user", id=uuid4())
    artifact = SqlArtifactRepository().create_version(owner_scope=source, schema_id="toonflow.world.v1", schema_version=1, content_json={"name": "shared"})
    resources = SqlResourceRepository()
    resource = resources.create(source, "world", artifact.artifact_version_id)
    frozen = resources.freeze(resource.resource_id, source, resources.get_draft(resource.resource_id, source).draft_version)
    grant = resources.grant(frozen.revision_id, source, consumer, capability_actions=["reference"])
    agent = _agent(consumer, "resource-consumer")
    ref = {"resource_id": str(resource.resource_id), "resource_type": "world", "revision_id": str(frozen.revision_id), "grant_snapshot_id": str(grant)}
    graph = {"nodes": [{"id": "use", "type": f"agent.invoke.{agent.revision_id}", "config": {
        "agent_revision_id": str(agent.revision_id), "resource_ref": ref,
    }}], "edges": []}
    # Publication-time graph authorization and typed compilation both accept
    # the currently active grant.
    _assert_graph_reference_authorization(graph, consumer)
    snapshot, _ = SqlRegistryService(get_session_factory()).create_snapshot(_agent_definitions_for_graph(graph, consumer))
    WorkflowCompiler().compile(workflow_revision_id=uuid4(), graph=graph, registry_snapshot=snapshot)
    _runtime, run_id = _run(consumer, graph, inputs={"world": ref})
    resources.revoke_grant(frozen.revision_id, grant, source)
    worker, atlas = _worker()
    attempt = _attempt(run_id, "use")
    _runtime.set_attempt_running(attempt.attempt_id, "resource-worker")
    with pytest.raises(ForbiddenError):
        worker.execute_attempt(attempt.attempt_id)
    assert atlas.calls == 0
    with get_session_factory()() as session:
        row = session.get(NodeRunAttemptModel, attempt.attempt_id)
        assert row is not None and row.status == AttemptStatus.FAILED
