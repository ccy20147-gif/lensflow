"""PG contract tests for owner-confirmed Architect proposals."""

from __future__ import annotations
import json
import os
from uuid import UUID, uuid4
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy import text
from src.core.exceptions import ConflictError, PolicyBlockedError, ValidationError_
from src.domain.agent.architect_service import ArchitectService
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.session import get_session_factory
from src.schemas.models import OwnerScope
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.infra.db.models import NodeRunAttemptModel, NodeRunModel, WorkflowRevisionModel, WorkflowRunModel
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.infra.db.registry_repository import SqlRegistryService
from src.core.config import settings
from src.schemas.models import NodeDefinitionRevision, PortTypeRef


@pytest.fixture
def factory():
    if os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1":
        pytest.skip("PG tests disabled")
    result = get_session_factory()
    with result() as session:
        session.execute(text("SELECT 1"))
    return result


def test_architect_applies_only_current_owner_confirmed_hash(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    registry = SqlRegistryService(factory)
    node_type = f"idea.{uuid4().hex}"
    definition = NodeDefinitionRevision(
        node_type_id=node_type, revision_id=uuid4(), semantic_version="1.0.0",
        executor_ref="workflow.idea", input_ports=[],
        output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="idea", schema_version=1, cardinality="optional")],
        config_schema={"type": "object"}, policy_metadata={"builtin": True},
    )
    registry.add_node_definition(definition)
    settings.registry_package_signing_key = "architect-test-key"
    registry.approve_node_definition(definition.revision_id, signing_key=settings.registry_package_signing_key)
    registry.activate_node_definition(definition.node_type_id, definition.revision_id)
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id,
        owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash,
        intent="add a typed node",
        operations=[{"op": "add_node", "node": {"id": "idea", "type": node_type}}],
    )
    assert service.diff(UUID(proposal["proposal_id"]))["summary"] == {"add_node": 1}
    with pytest.raises(ConflictError):
        service.apply(
            proposal_id=UUID(proposal["proposal_id"]),
            owner_scope=owner.scoped_id,
            base_draft_hash=draft.graph_hash,
            validated_plan_hash="bad",
        )
    applied = service.apply(
        proposal_id=UUID(proposal["proposal_id"]),
        owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash,
        validated_plan_hash=proposal["validation"]["validated_plan_hash"],
    )
    assert applied["state"] == "applied"
    assert applied["approval"]["idempotency_key"] == "legacy-service-call"
    assert service.apply(
        proposal_id=UUID(proposal["proposal_id"]), owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash,
        validated_plan_hash=proposal["validation"]["validated_plan_hash"],
    )["applied_draft_hash"] == applied["applied_draft_hash"]
    assert workflows.get_draft(workflow.workflow_id).graph["nodes"][0]["id"] == "idea"


def test_architect_rejects_implicit_capability(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    with pytest.raises(ValidationError_):
        ArchitectService(factory).create(
            workflow_id=workflow.workflow_id,
            owner_scope=owner.scoped_id,
            base_draft_hash=draft.graph_hash,
            intent="bad",
            operations=[{"op": "add_node", "node": {"id": "x", "type": "latest"}}],
        )


def test_architect_patch_cannot_remove_required_human_gate(factory):
    """Architect applies through the Draft invariant rather than bypassing it."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    initial = workflows.get_draft(workflow.workflow_id)
    draft = workflows.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{
            "id": "policy-gate", "type": "human_gate",
            "config": {"policy_strength": "policy_required", "timeout_minutes": 5},
        }], "edges": []},
        config={}, layout={}, base_graph_hash=initial.graph_hash,
    )
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id, owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash, intent="remove safety gate",
        operations=[{"op": "remove_node", "node_id": "policy-gate"}],
    )
    with pytest.raises(ConflictError):
        service.apply(
            proposal_id=UUID(proposal["proposal_id"]), owner_scope=owner.scoped_id,
            base_draft_hash=draft.graph_hash,
            validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        )
    assert workflows.get_draft(workflow.workflow_id).graph["nodes"][0]["id"] == "policy-gate"


def test_architect_apply_persists_current_budget_and_material_validation(factory):
    """Confirmation must re-run, persist and block host gates before CAS."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    node_type = f"gated.{uuid4().hex}"
    registry = SqlRegistryService(factory)
    definition = NodeDefinitionRevision(
        node_type_id=node_type, revision_id=uuid4(), semantic_version="1.0.0",
        executor_ref="workflow.gated", input_ports=[],
        output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="gated", schema_version=1, cardinality="optional")],
        config_schema={"type": "object"}, policy_metadata={"builtin": True, "cost_estimate": 11, "max_cost": 10, "material_gate_required": True},
    )
    registry.add_node_definition(definition)
    settings.registry_package_signing_key = "architect-test-key"
    registry.approve_node_definition(definition.revision_id, signing_key=settings.registry_package_signing_key)
    registry.activate_node_definition(node_type, definition.revision_id)
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id, owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash, intent="gated",
        operations=[{"op": "add_node", "node": {"id": "gated", "type": node_type, "config": {}}}],
    )
    with pytest.raises(ConflictError):
        service.apply(
            proposal_id=UUID(proposal["proposal_id"]), owner_scope=owner.scoped_id,
            base_draft_hash=draft.graph_hash,
            validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        )
    persisted = service.latest(UUID(proposal["proposal_id"]))
    assert persisted["state"] == "invalid"
    assert persisted["validation"]["entitlement_errors"]
    assert persisted["validation"]["material_gate_errors"]
    assert workflows.get_draft(workflow.workflow_id).graph == {}


def _attempt(factory, owner: OwnerScope):
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    with factory.begin() as session:
        now = datetime.now(timezone.utc)
        revision = WorkflowRevisionModel(revision_id=uuid4(), workflow_id=workflow.workflow_id, revision_number=1,
            graph_hash="runtime", execution_hash="runtime", registry_snapshot_id=uuid4(), graph={}, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=now)
        session.add(revision)
        session.flush()
        run = WorkflowRunModel(run_id=uuid4(), workflow_revision_id=revision.revision_id, compiled_plan_id=uuid4(), owner_scope=owner.scoped_id, input_snapshot={}, status=RunStatus.RUNNING, created_at=now)
        session.add(run)
        session.flush()
        node = NodeRunModel(node_run_id=uuid4(), run_id=run.run_id, node_instance_id="architect", node_type_id="agent_invoke", status=NodeRunStatus.RUNNING)
        session.add(node)
        session.flush()
        attempt = NodeRunAttemptModel(attempt_id=uuid4(), node_run_id=node.node_run_id, status=AttemptStatus.RUNNING, fixed_input={})
        session.add(attempt)
    return attempt.attempt_id


def test_architect_generate_uses_fixed_agent_invoke_not_client_operations(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    draft = SqlWorkflowService(factory).get_draft(workflow.workflow_id)

    class FakeTransport:
        def request(self, method, url, **kwargs):
            return httpx.Response(200, request=httpx.Request(method, url), json={
                "choices": [{"message": {"content": json.dumps({"operations": [{"op": "add_node", "node": {"id": "typed", "type": "brief"}}]})}}],
            })

    invocations = AgentInvocationService(factory, adapter=AtlasCloudAdapter(transport=FakeTransport(), api_key="test", base_url="https://atlas.test"))
    result = ArchitectService(factory, invocation_service=invocations).generate(
        workflow_id=workflow.workflow_id, owner_scope=owner.scoped_id, base_draft_hash=draft.graph_hash,
        intent="add a brief", node_run_attempt_id=_attempt(factory, owner),
    )
    assert result["generation"]["agent_revision_id"]
    assert result["operations"] == [{"op": "add_node", "node": {"id": "typed", "type": "brief"}}]


def test_architect_intent_creates_server_owned_isolated_attempt(factory):
    """Canvas intent never supplies an attempt id or graph operations."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    draft = SqlWorkflowService(factory).get_draft(workflow.workflow_id)

    class FakeTransport:
        def request(self, method, url, **kwargs):
            return httpx.Response(200, request=httpx.Request(method, url), json={
                "choices": [{"message": {"content": json.dumps({"operations": [{"op": "add_node", "node": {"id": "intent-typed", "type": "brief"}}]})}}],
            })

    invocations = AgentInvocationService(factory, adapter=AtlasCloudAdapter(
        transport=FakeTransport(), api_key="test", base_url="https://atlas.test",
    ))
    result = ArchitectService(factory, invocation_service=invocations).generate_from_intent(
        workflow_id=workflow.workflow_id, owner_scope=owner.scoped_id,
        base_draft_hash=draft.graph_hash, intent="add a brief",
    )
    assert result["operations"][0]["node"]["id"] == "intent-typed"
    with factory() as session:
        attempt = session.get(NodeRunAttemptModel, UUID(result["runtime_attempt_id"]))
        run = session.get(WorkflowRunModel, UUID(result["runtime_run_id"]))
        assert attempt is not None and attempt.fixed_input["architect_source_workflow_id"] == str(workflow.workflow_id)
        assert run is not None and run.owner_scope == owner.scoped_id


def test_architect_intent_without_provider_does_not_materialize_run(factory, monkeypatch):
    """Missing credentials fail before an isolated attempt can be persisted."""
    monkeypatch.setattr(settings, "atlascloud_api_key", "")
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    draft = SqlWorkflowService(factory).get_draft(workflow.workflow_id)
    with factory() as session:
        before = session.query(WorkflowRunModel).count()
    with pytest.raises(PolicyBlockedError):
        ArchitectService(factory).generate_from_intent(
            workflow_id=workflow.workflow_id, owner_scope=owner.scoped_id,
            base_draft_hash=draft.graph_hash, intent="add a brief",
        )
    with factory() as session:
        assert session.query(WorkflowRunModel).count() == before
