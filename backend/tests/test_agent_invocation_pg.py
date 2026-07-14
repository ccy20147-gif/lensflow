"""PG execution contracts for frozen AgentInvoke through AtlasCloud."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text

from src.core.exceptions import CrossOwnerError, ForbiddenError, PolicyBlockedError, ValidationError_
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.agent.tool_broker import ToolBroker
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.skill_repository import SqlSkillRepository
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.models import (
    ArtifactVersionModel, NodeRunAttemptModel, NodeRunModel, OutboxEventModel,
    ProviderInvocationRecordModel, ProviderOutputBindingModel, WorkflowRevisionModel,
    WorkflowRunModel,
    ToolDefinitionModel, ToolRevisionModel, ToolInvocationModel,
)
from cryptography.fernet import Fernet
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


class InspectingTransport:
    def __init__(self, factory, response: dict):
        self.factory = factory
        self.response = response
        self.called = False
        self.dispatch_was_committed = False

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.called = True
        with self.factory() as session:
            self.dispatch_was_committed = bool(session.scalar(select(func.count()).select_from(OutboxEventModel).where(OutboxEventModel.purpose == "provider_dispatch")))
        request = httpx.Request(method, url)
        return httpx.Response(200, request=request, json=self.response, headers={"x-request-id": "atlas-test"})


@pytest.fixture
def factory():
    result = get_session_factory()
    with result() as session:
        session.execute(text("SELECT 1"))
    return result


def _attempt(factory, owner: OwnerScope):
    from src.domain.workflow.sql_workflow_service import SqlWorkflowService
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    with factory.begin() as session:
        now = datetime.now(timezone.utc)
        revision = WorkflowRevisionModel(revision_id=uuid4(), workflow_id=workflow.workflow_id, revision_number=1, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph={}, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=now)
        session.add(revision)
        session.flush()
        run = WorkflowRunModel(run_id=uuid4(), workflow_revision_id=revision.revision_id, compiled_plan_id=uuid4(), owner_scope=owner.scoped_id, input_snapshot={}, status=RunStatus.RUNNING, created_at=now)
        session.add(run)
        session.flush()
        node = NodeRunModel(node_run_id=uuid4(), run_id=run.run_id, node_instance_id="agent", node_type_id="agent_invoke", status=NodeRunStatus.RUNNING)
        session.add(node)
        session.flush()
        attempt = NodeRunAttemptModel(attempt_id=uuid4(), node_run_id=node.node_run_id, status=AttemptStatus.RUNNING, fixed_input={})
        session.add(attempt)
    return attempt.attempt_id


def _agent(factory, owner: OwnerScope, *, output_schema: str = "agent_output.v1"):
    repo = SqlAgentRepository(factory)
    definition = repo.create_definition(name="invoke", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    body = {"output_schema_ref": output_schema, "sop_steps": [{"step_id": "s", "instruction": "Return object"}], "execution_policy": {"provider_ref": "atlascloud/qwen-test"}}
    revision = repo.create_revision(definition.agent_id, body)
    repo.promote_revision(revision.revision_id)
    return repo, definition, revision, body


def _service(factory, response: dict) -> tuple[AgentInvocationService, InspectingTransport]:
    transport = InspectingTransport(factory, response)
    return AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=transport, api_key="test-key", base_url="https://atlas.test")), transport


def test_verified_output_persists_artifact_binding_and_dispatch_precedes_network(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, _, revision, _ = _agent(factory, owner)
    attempt_id = _attempt(factory, owner)
    service, transport = _service(factory, {"id": f"r-{uuid4()}", "model_version": "qwen-test", "data": [{"answer": "ok"}], "usage": {"tokens": 3}})
    result = service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={"prompt": "x"}, idempotency_key=f"agent-{uuid4()}")
    assert result["status"] == "completed" and transport.called and transport.dispatch_was_committed
    with factory() as session:
        artifact = session.get(ArtifactVersionModel, result["artifact_version_ids"][0])
        assert artifact is not None and artifact.schema_id == "agent_output"
        record = session.get(ProviderInvocationRecordModel, result["record_id"])
        assert record is not None
        assert session.scalar(select(func.count()).select_from(ProviderOutputBindingModel).where(ProviderOutputBindingModel.record_id == record.record_id)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEventModel).where(OutboxEventModel.purpose == "result_publish")) >= 1
        traces = list(session.scalars(select(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
        )))
        assert {trace.content_json["phase"] for trace in traces} >= {"started", "completed"}
        assert all("prompt" not in trace.content_json for trace in traces)


def test_invalid_provider_output_creates_no_artifact_or_result_record(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, _, revision, _ = _agent(factory, owner)
    attempt_id = _attempt(factory, owner)
    service, _ = _service(factory, {"data": ["not-an-object"]})
    with factory() as session:
        records_before = session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel))
        artifacts_before = session.scalar(select(func.count()).select_from(ArtifactVersionModel).where(
            ArtifactVersionModel.owner_scope == owner.scoped_id,
            ArtifactVersionModel.schema_id != "toonflow.agent_sop_trace",
        ))
    with pytest.raises(ValidationError_):
        service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={}, idempotency_key=f"bad-{uuid4()}")
    with factory() as session:
        # A scrubbed SOP trace is audit evidence, not a provider output.  No
        # typed business artifact may be published for invalid provider JSON.
        assert session.scalar(select(func.count()).select_from(ArtifactVersionModel).where(
            ArtifactVersionModel.owner_scope == owner.scoped_id,
            ArtifactVersionModel.schema_id != "toonflow.agent_sop_trace",
        )) == artifacts_before
        assert session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel)) == records_before


def test_agent_execution_persists_frozen_skill_assembly_in_sop_trace(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    skills = SqlSkillRepository(factory)
    draft = skills.create_skill(name="method", description="", owner_scope=owner.scoped_id, body={"instructions": ["be concise"]})
    skill_revision = skills.submit_revision(draft.skill_id, base_hash=draft.content_hash)
    agents = SqlAgentRepository(factory)
    definition = agents.create_definition(name="with-skill", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = agents.create_revision(definition.agent_id, {
        "skill_revision_refs": [str(skill_revision.revision_id)], "output_schema_ref": "agent_output.v1",
        "sop_steps": [{"step_id": "s", "instruction": "Return object"}],
        "execution_policy": {"provider_ref": "atlascloud/qwen-test", "max_skill_tokens": 256},
    })
    agents.promote_revision(revision.revision_id)
    attempt_id = _attempt(factory, owner)
    service, _ = _service(factory, {"data": [{"answer": "ok"}]})
    service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={}, idempotency_key=f"skill-{uuid4()}")
    with factory() as session:
        traces = list(session.scalars(select(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
            ArtifactVersionModel.metadata_json["agent_revision_id"].as_string() == str(revision.revision_id),
        )))
        started = next(trace for trace in traces if trace.content_json["phase"] == "started")
        assert started.content_json["skill_assembly_plan_id"]
        assert started.content_json["skill_assembly_fingerprint"]


def test_cross_owner_skill_resource_ref_is_pinned_rechecked_and_revoke_safe(factory):
    """A grant-bearing SkillRef survives in the Agent revision, not just preview.

    The first invocation proves the running assembler resolves the fixed
    ResourceRef.  Revocation then blocks a *new* invocation while the first
    run's immutable SOP trace remains available to its own owner.
    """
    source = OwnerScope(kind="user", id=uuid4())
    consumer = OwnerScope(kind="user", id=uuid4())
    skills = SqlSkillRepository(factory)
    skill = skills.create_skill(
        name="licensed-method", description="", owner_scope=source.scoped_id,
        body={"instructions": ["Return concise structured output"]},
    )
    frozen = skills.submit_revision(skill.skill_id, base_hash=skill.content_hash)
    resources = SqlResourceRepository(factory)
    grant = resources.grant(frozen.revision_id, source, consumer, capability_actions=["reference", "execute"])
    ref = {
        "resource_id": str(skill.skill_id), "resource_type": "skill",
        "revision_id": str(frozen.revision_id), "grant_snapshot_id": str(grant),
    }

    agents = SqlAgentRepository(factory)
    definition = agents.create_definition(
        name="licensed-agent", description="", agent_kind="configurable",
        owner_scope=consumer.scoped_id,
    )
    revision = agents.create_revision(definition.agent_id, {
        "skill_revision_refs": [ref], "output_schema_ref": "agent_output.v1",
        "sop_steps": [{"step_id": "s", "instruction": "Return object"}],
        "execution_policy": {"provider_ref": "atlascloud/qwen-test", "max_skill_tokens": 256},
    })
    agents.promote_revision(revision.revision_id)
    assert revision.skill_revision_refs[0].grant_snapshot_id == grant

    service, _ = _service(factory, {"data": [{"answer": "ok"}]})
    first_attempt = _attempt(factory, consumer)
    result = service.execute(
        agent_revision_id=revision.revision_id, owner_scope=consumer,
        node_run_attempt_id=first_attempt, typed_inputs={}, idempotency_key=f"licensed-{uuid4()}",
    )
    assert result["status"] == "completed"
    with factory() as session:
        historical_trace_count = session.scalar(select(func.count()).select_from(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
            ArtifactVersionModel.owner_scope == consumer.scoped_id,
        ))
    assert historical_trace_count and historical_trace_count > 0

    # A generic grant deliberately does not imply package redistribution.
    with pytest.raises(CrossOwnerError):
        resources.resolve_ref(skill.skill_id, frozen.revision_id, consumer, grant, required_actions={"redistribute"})
    resources.revoke_grant(frozen.revision_id, grant, source)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == grant,
            OutboxEventModel.event_type == "resource_grant.revoked",
        )) == 1

    with pytest.raises(ForbiddenError):
        service.execute(
            agent_revision_id=revision.revision_id, owner_scope=consumer,
            node_run_attempt_id=_attempt(factory, consumer), typed_inputs={}, idempotency_key=f"revoked-{uuid4()}",
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
            ArtifactVersionModel.owner_scope == consumer.scoped_id,
        )) == historical_trace_count


def test_skill_grant_requires_explicit_redistribute_action(factory):
    source = OwnerScope(kind="user", id=uuid4())
    consumer = OwnerScope(kind="user", id=uuid4())
    skills = SqlSkillRepository(factory)
    draft = skills.create_skill(name="packageable", description="", owner_scope=source.scoped_id, body={"instructions": ["x"]})
    revision = skills.submit_revision(draft.skill_id, base_hash=draft.content_hash)
    resources = SqlResourceRepository(factory)
    grant = resources.grant(revision.revision_id, source, consumer, capability_actions=["reference", "execute", "redistribute"])
    assert resources.resolve_ref(draft.skill_id, revision.revision_id, consumer, grant, required_actions={"redistribute"}).grant_snapshot_id == grant


def test_agent_input_resource_ref_is_rechecked_and_trace_is_scrubbed(factory):
    source = OwnerScope(kind="user", id=uuid4())
    consumer = OwnerScope(kind="user", id=uuid4())
    skills = SqlSkillRepository(factory)
    draft = skills.create_skill(name="input-resource", description="", owner_scope=source.scoped_id, body={"instructions": ["source"]})
    frozen = skills.submit_revision(draft.skill_id, base_hash=draft.content_hash)
    resources = SqlResourceRepository(factory)
    grant = resources.grant(frozen.revision_id, source, consumer, capability_actions=["reference"])
    ref = {
        "resource_id": str(draft.skill_id), "resource_type": "skill",
        "revision_id": str(frozen.revision_id), "grant_snapshot_id": str(grant),
    }
    _, _, revision, _ = _agent(factory, consumer)
    service, _ = _service(factory, {"data": [{"answer": "ok"}]})
    service.execute(
        agent_revision_id=revision.revision_id, owner_scope=consumer,
        node_run_attempt_id=_attempt(factory, consumer), typed_inputs={"knowledge": ref, "prompt": "do not persist me"},
        idempotency_key=f"input-ref-{uuid4()}",
    )
    with factory() as session:
        started = session.scalar(select(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
            ArtifactVersionModel.metadata_json["agent_revision_id"].as_string() == str(revision.revision_id),
            ArtifactVersionModel.metadata_json["phase"].as_string() == "started",
        ).order_by(ArtifactVersionModel.created_at.desc()))
        assert started is not None
        assert started.content_json["input_resource_refs"] == [{
            "resource_id": str(draft.skill_id), "resource_type": "skill",
            "revision_id": str(frozen.revision_id), "grant_snapshot_id": str(grant),
        }]
        assert "do not persist me" not in str(started.content_json)
    resources.revoke_grant(frozen.revision_id, grant, source)
    with pytest.raises(ForbiddenError):
        service.execute(
            agent_revision_id=revision.revision_id, owner_scope=consumer,
            node_run_attempt_id=_attempt(factory, consumer), typed_inputs={"knowledge": ref},
            idempotency_key=f"input-ref-revoked-{uuid4()}",
        )


def test_frozen_json_output_schema_rejects_object_with_missing_required_field(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    repo = SqlAgentRepository(factory)
    definition = repo.create_definition(name="schema", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(definition.agent_id, {
        "output_schema_ref": "agent_output.v1",
        "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False},
        "sop_steps": [{"step_id": "s", "instruction": "Return answer"}],
        "execution_policy": {"provider_ref": "atlascloud/qwen-test"},
    })
    repo.promote_revision(revision.revision_id)
    service, _ = _service(factory, {"data": [{"wrong": "shape"}]})
    with pytest.raises(ValidationError_, match="missing required field"):
        service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=_attempt(factory, owner), typed_inputs={}, idempotency_key=f"schema-{uuid4()}")


def test_missing_credential_and_cross_owner_block_before_dispatch(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, _, revision, _ = _agent(factory, owner)
    attempt_id = _attempt(factory, owner)
    blocked = AgentInvocationService(factory, adapter=AtlasCloudAdapter(api_key=""))
    with factory() as session:
        dispatch_before = session.scalar(select(func.count()).select_from(OutboxEventModel).where(OutboxEventModel.purpose == "provider_dispatch"))
    with pytest.raises(PolicyBlockedError):
        blocked.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={}, idempotency_key=f"none-{uuid4()}")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxEventModel).where(OutboxEventModel.purpose == "provider_dispatch")) == dispatch_before
    service, transport = _service(factory, {"data": [{"answer": "no"}]})
    with pytest.raises(ForbiddenError):
        service.execute(agent_revision_id=revision.revision_id, owner_scope=OwnerScope(kind="user", id=uuid4()), node_run_attempt_id=attempt_id, typed_inputs={}, idempotency_key=f"owner-{uuid4()}")
    assert not transport.called

    # Owning the Agent is insufficient: the durable attempt itself must have
    # the same owner before any trace, outbox, or Atlas submission.
    foreign_attempt = _attempt(factory, OwnerScope(kind="user", id=uuid4()))
    with pytest.raises(ForbiddenError):
        service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=foreign_attempt, typed_inputs={}, idempotency_key=f"foreign-attempt-{uuid4()}")
    assert not transport.called


def test_replay_stays_bound_to_revision_a_after_b_active(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    repo, definition, revision_a, body = _agent(factory, owner, output_schema="schema_a.v1")
    revision_b = repo.create_revision(definition.agent_id, {**body, "output_schema_ref": "schema_b.v1"}, base_hash=revision_a.content_hash)
    repo.promote_revision(revision_b.revision_id)
    service, _ = _service(factory, {"data": [{"answer": "a"}]})
    result = service.execute(agent_revision_id=revision_a.revision_id, owner_scope=owner, node_run_attempt_id=_attempt(factory, owner), typed_inputs={}, idempotency_key=f"replay-{uuid4()}")
    with factory() as session:
        assert session.get(ArtifactVersionModel, result["artifact_version_ids"][0]).schema_id == "schema_a"


def test_agent_tool_result_rejoins_parent_attempt_and_schedules(factory, monkeypatch):
    monkeypatch.setattr("src.domain.agent.tool_broker.socket.getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("8.8.8.8", 443))])
    owner = OwnerScope(kind="user", id=uuid4())
    with factory.begin() as session:
        tool = ToolDefinitionModel(tool_id=uuid4(), name="safe", owner_scope=owner.scoped_id, provider_type="atlascloud")
        tool_revision = ToolRevisionModel(revision_id=uuid4(), tool_id=tool.tool_id, revision_number=1, status="active", approval_status="approved", body={"risk_level": "low", "data_classifications": ["internal"], "sanitizer_policy": {"policy_version": "platform.v1"}, "operations": [{"id": "generate", "input_schema": {}, "output_schema": {"type": "object"}, "output_schema_ref": "tool_output.v1", "disclosure_fields": ["prompt"], "endpoint": "https://api.atlascloud.ai/tool", "execution_limits": {"max_calls_per_step": 5, "max_calls_per_run": 10, "max_concurrency": 3, "max_cost": 10, "max_retries": 0, "cost_estimate": 0.1}}], "egress_policy": {"allowed_domains": ["api.atlascloud.ai"], "timeout_seconds": 20, "max_request_bytes": 1000000, "max_response_bytes": 1000000}})
        session.add_all([tool, tool_revision])
    repo = SqlAgentRepository(factory)
    definition = repo.create_definition(name="tools", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(definition.agent_id, {"tool_revision_refs": [str(tool_revision.revision_id)], "tool_access_plan": [{"tool_revision_id": str(tool_revision.revision_id), "operations": [{"operation_id": "generate", "allowed_scopes": ["generate"], "disclosure_fields": ["prompt"]}]}], "sop_steps": [{"step_id": "s", "instruction": "Use approved tool when needed"}], "execution_policy": {"provider_ref": "atlascloud/qwen-test"}})
    repo.promote_revision(revision.revision_id)
    broker = ToolBroker(factory, encryption_key=Fernet.generate_key().decode())
    binding = broker.bind(owner_scope=owner.scoped_id, tool_revision_id=tool_revision.revision_id, scopes=["generate"], secret="secret")
    transport = InspectingTransport(factory, {"data": [{"tool_calls": [{"tool_revision_id": str(tool_revision.revision_id), "operation_id": "generate", "requested_scopes": ["generate"], "input": {"prompt": "x"}, "disclosure_fields": ["prompt"]}]}]})
    service = AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test"), tool_broker=broker)
    attempt_id = _attempt(factory, owner)
    result = service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={"tool_bindings": {str(tool_revision.revision_id): str(binding.binding_id)}}, idempotency_key=f"tool-{uuid4()}")
    assert result["status"] == "waiting_tool" and len(result["tool_dispatches"]) == 1
    with factory() as session:
        invocation = session.get(ToolInvocationModel, result["tool_dispatches"][0]["tool_invocation_id"])
        assert invocation is not None and invocation.status == "dispatched" and invocation.node_run_attempt_id is not None
        assert session.get(NodeRunAttemptModel, attempt_id).status == AttemptStatus.WAITING_EXTERNAL
    tool_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'{"ok": true}', headers={"content-type": "application/json"})))
    assert broker.consume_dispatch_event(UUID(result["tool_dispatches"][0]["dispatch_event_id"]), transport=tool_client) == "completed"
    with factory() as session:
        assert session.get(NodeRunAttemptModel, attempt_id).status == AttemptStatus.COMPLETED


def test_agent_tool_unknown_reconcile_and_cancel_settle_parent_attempt(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    with factory.begin() as session:
        tool = ToolDefinitionModel(tool_id=uuid4(), name="safe-terminal", owner_scope=owner.scoped_id, provider_type="atlascloud")
        tool_revision = ToolRevisionModel(revision_id=uuid4(), tool_id=tool.tool_id, revision_number=1, status="active", approval_status="approved", body={"risk_level": "low", "data_classifications": ["internal"], "sanitizer_policy": {"policy_version": "platform.v1"}, "operations": [{"id": "generate", "input_schema": {}, "output_schema": {"type": "object"}, "disclosure_fields": ["prompt"], "endpoint": "https://api.atlascloud.ai/tool", "execution_limits": {"max_calls_per_step": 5, "max_calls_per_run": 10, "max_concurrency": 3, "max_cost": 10, "max_retries": 0, "cost_estimate": 0.1}}], "egress_policy": {"allowed_domains": ["api.atlascloud.ai"], "timeout_seconds": 20, "max_request_bytes": 1000000, "max_response_bytes": 1000000}})
        session.add_all([tool, tool_revision])
    repo = SqlAgentRepository(factory)
    definition = repo.create_definition(name="tools-terminal", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(definition.agent_id, {"tool_revision_refs": [str(tool_revision.revision_id)], "tool_access_plan": [{"tool_revision_id": str(tool_revision.revision_id), "operations": [{"operation_id": "generate", "allowed_scopes": ["generate"], "disclosure_fields": ["prompt"]}]}], "sop_steps": [{"step_id": "s", "instruction": "Use approved tool"}], "execution_policy": {"provider_ref": "atlascloud/qwen-test"}})
    repo.promote_revision(revision.revision_id)
    broker = ToolBroker(factory, encryption_key=Fernet.generate_key().decode())
    binding = broker.bind(owner_scope=owner.scoped_id, tool_revision_id=tool_revision.revision_id, scopes=["generate"], secret="secret")
    response = {"data": [{"tool_calls": [{"tool_revision_id": str(tool_revision.revision_id), "operation_id": "generate", "requested_scopes": ["generate"], "input": {"prompt": "x"}, "disclosure_fields": ["prompt"]}]}]}
    service = AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=InspectingTransport(factory, response), api_key="key", base_url="https://atlas.test"), tool_broker=broker)
    attempt_id = _attempt(factory, owner)
    result = service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=attempt_id, typed_inputs={"tool_bindings": {str(tool_revision.revision_id): str(binding.binding_id)}}, idempotency_key=f"unknown-{uuid4()}")
    invocation = UUID(result["tool_dispatches"][0]["tool_invocation_id"])
    broker.mark_unknown(invocation)
    with factory() as session:
        assert session.get(NodeRunAttemptModel, attempt_id).status == AttemptStatus.WAITING_EXTERNAL
    broker.reconcile(invocation, result_fingerprint="reconciled", completed=True)
    with factory() as session:
        assert session.get(NodeRunAttemptModel, attempt_id).status == AttemptStatus.COMPLETED

    # A fresh call cancelled before egress fails the parent; a subsequent late
    # result is quarantined rather than overwriting that terminal decision.
    next_attempt = _attempt(factory, owner)
    next_binding = broker.bind(owner_scope=owner.scoped_id, tool_revision_id=tool_revision.revision_id, scopes=["generate"], secret="secret-2")
    next_result = service.execute(agent_revision_id=revision.revision_id, owner_scope=owner, node_run_attempt_id=next_attempt, typed_inputs={"tool_bindings": {str(tool_revision.revision_id): str(next_binding.binding_id)}}, idempotency_key=f"cancel-{uuid4()}")
    next_invocation = UUID(next_result["tool_dispatches"][0]["tool_invocation_id"])
    broker.cancel(next_invocation, owner_scope=owner.scoped_id)
    broker.mark_unknown(next_invocation)
    with factory() as session:
        assert session.get(NodeRunAttemptModel, next_attempt).status == AttemptStatus.FAILED
        assert session.get(ToolInvocationModel, next_invocation).late_result_quarantined
