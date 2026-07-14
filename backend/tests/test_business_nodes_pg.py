"""PostgreSQL contract coverage for TF-WF-010 public business nodes."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from src.core.exceptions import ForbiddenError, ValidationError_
from src.domain.workflow.business_node_service import BUSINESS_NODE_CATALOG, BusinessNodeService
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.models import NodeRunAttemptModel, NodeRunModel, ResourceDraftModel, ResourceRevisionModel, WorkflowRevisionModel, WorkflowRunModel
from src.infra.db.session import get_session_factory
from src.infra.db.identity_repository import get_session_store
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


@pytest.fixture
def factory():
    result = get_session_factory()
    with result() as session:
        session.execute(text("SELECT 1"))
    return result


def _run_context(factory, owner: OwnerScope, node_type: str = "workbench_task", config: dict | None = None):
    from src.domain.workflow.sql_workflow_service import SqlWorkflowService
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    with factory.begin() as session:
        now = datetime.now(timezone.utc)
        revision = WorkflowRevisionModel(revision_id=uuid4(), workflow_id=workflow.workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph={"nodes": [{"id": "wb", "type": node_type, "config": config or {}}], "edges": []}, config={}, layout={},
            revision_status=RevisionStatus.ACTIVE, created_at=now)
        run = WorkflowRunModel(run_id=uuid4(), workflow_revision_id=revision.revision_id, compiled_plan_id=uuid4(),
            owner_scope=owner.scoped_id, input_snapshot={}, status=RunStatus.RUNNING, created_at=now)
        node = NodeRunModel(node_run_id=uuid4(), run_id=run.run_id, node_instance_id="wb", node_type_id=node_type, status=NodeRunStatus.RUNNING)
        # These ORM rows have scalar FKs rather than relationships, so flush
        # the parent chain before adding the attempt. This mirrors runtime
        # persistence and makes the database constraint part of the test.
        session.add(revision)
        session.flush()
        session.add(run)
        session.flush()
        session.add(node)
        session.flush()
        attempt = NodeRunAttemptModel(attempt_id=uuid4(), node_run_id=node.node_run_id, status=AttemptStatus.RUNNING, fixed_input={})
        session.add(attempt)
    return revision, run, node, attempt


def test_candidate_selection_keeps_fixed_refs_and_owner(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    artifacts = SqlArtifactRepository(factory)
    first = artifacts.create_version(owner_scope=owner, schema_id="image", schema_version=1, content_json={})
    second = artifacts.create_version(owner_scope=owner, schema_id="image", schema_version=1, content_json={})
    service = BusinessNodeService(factory)
    candidate_set = service.create_candidate_set(owner_scope=owner.scoped_id, candidate_version_ids=[first.artifact_version_id, second.artifact_version_id], failed_candidates=[{"index": 2, "code": "provider_failed"}], cost_allocation={"shared": 0.5})
    selection = service.select(candidate_set_id=candidate_set.candidate_set_id, owner_scope=owner.scoped_id, ranking=[second.artifact_version_id, first.artifact_version_id], selected_version_ids=[second.artifact_version_id], actor_or_model="user", rubric_revision="rubric.v1", rationale="better framing")
    assert selection.selected_refs[0]["artifact_version_id"] == str(second.artifact_version_id)
    with pytest.raises(ValidationError_):
        service.select(candidate_set_id=candidate_set.candidate_set_id, owner_scope=owner.scoped_id, ranking=[], selected_version_ids=[uuid4()], actor_or_model="user", rubric_revision="rubric.v1", rationale="")


def test_workbench_task_commits_only_schema_valid_output_with_cas(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    revision, run, node, attempt = _run_context(factory, owner)
    artifacts = SqlArtifactRepository(factory)
    output = artifacts.create_version(owner_scope=owner, schema_id="shot_plan", schema_version=1, content_json={})
    service = BusinessNodeService(factory)
    task = service.create_workbench_task(owner_scope=owner.scoped_id, workflow_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id, input_snapshot_refs=[], target_workbench="shot-plan", output_schema_ref="shot_plan.v1", resource_type="shot_plan")
    with factory.begin() as session:
        stored_revision = session.get(WorkflowRevisionModel, revision.revision_id)
        stored_revision.graph = {"nodes": [{"id": "wb", "type": "workbench_task"}, {"id": "next", "type": "provider"}], "edges": [{"source": "wb", "target": "next"}]}
        downstream = NodeRunModel(node_run_id=uuid4(), run_id=run.run_id, node_instance_id="next", node_type_id="provider", status=NodeRunStatus.PENDING)
        session.add(downstream)
        session.flush()
        assert session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == downstream.node_run_id)) is None
    commits = service.submit_workbench_task(task_id=task.task_id, owner_scope=owner.scoped_id, actor_id=owner.id, task_version=1, idempotency_token="token-123", output_artifact_version_ids=[output.artifact_version_id])
    assert commits[0].source_artifact_version_id == output.artifact_version_id
    with factory() as session:
        revision = session.get(ResourceRevisionModel, commits[0].revision_id)
        draft = session.get(ResourceDraftModel, commits[0].resource_id)
        assert revision is not None and revision.content_artifact_version_id == output.artifact_version_id
        assert draft is not None and draft.base_revision_id == commits[0].revision_id and draft.draft_version == 1
        downstream = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "next"))
        downstream_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == downstream.node_run_id))
        assert downstream is not None and downstream.status == NodeRunStatus.READY
        assert downstream_attempt.fixed_input["committed_resource_refs"] == [{"resource_id": str(commits[0].resource_id), "resource_type": "shot_plan", "revision_id": str(commits[0].revision_id)}]
    again = service.submit_workbench_task(task_id=task.task_id, owner_scope=owner.scoped_id, actor_id=owner.id, task_version=1, idempotency_token="token-123", output_artifact_version_ids=[output.artifact_version_id])
    assert again[0].revision_id == commits[0].revision_id
    # A distinct task cannot overwrite the now-advanced Draft using a stale
    # expected version, even though it owns the same resource and artifact.
    revision2, run2, node2, attempt2 = _run_context(factory, owner)
    stale = service.create_workbench_task(owner_scope=owner.scoped_id, workflow_revision_id=revision2.revision_id, run_id=run2.run_id, node_run_id=node2.node_run_id, attempt_id=attempt2.attempt_id, input_snapshot_refs=[], target_workbench="shot-plan", output_schema_ref="shot_plan.v1", resource_type="shot_plan", expected_draft_version=0)
    with pytest.raises(Exception, match="CAS"):
        service.submit_workbench_task(task_id=stale.task_id, owner_scope=owner.scoped_id, actor_id=owner.id, task_version=1, idempotency_token="token-456", output_artifact_version_ids=[output.artifact_version_id], resource_id=commits[0].resource_id)


def test_workbench_task_rejects_non_workflow_node_and_cross_owner(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    revision, run, node, attempt = _run_context(factory, owner)
    with factory.begin() as session:
        stored = session.get(NodeRunModel, node.node_run_id)
        assert stored is not None
        stored.node_type_id = "agent_invoke"
    service = BusinessNodeService(factory)
    with pytest.raises(ValidationError_):
        service.create_workbench_task(owner_scope=owner.scoped_id, workflow_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id, input_snapshot_refs=[], target_workbench="x", output_schema_ref="x.v1", resource_type="x")
    with pytest.raises(ForbiddenError):
        service.create_workbench_task(owner_scope=f"user:{uuid4()}", workflow_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id, input_snapshot_refs=[], target_workbench="x", output_schema_ref="x.v1", resource_type="x")


def test_business_node_api_ignores_spoofed_owner_scope(factory):
    """A bearer principal cannot claim the artifact owner's scope in JSON."""
    from src.app import app

    owner = OwnerScope(kind="user", id=uuid4())
    attacker = OwnerScope(kind="user", id=uuid4())
    artifact = SqlArtifactRepository(factory).create_version(owner_scope=owner, schema_id="image", schema_version=1, content_json={})
    owner_token = get_session_store().issue(owner.id)["token"]
    attacker_token = get_session_store().issue(attacker.id)["token"]
    with TestClient(app) as client:
        created = client.post("/api/v1/business-nodes/candidate-sets", headers={"Authorization": f"Bearer {owner_token}"}, json={"candidate_version_ids": [str(artifact.artifact_version_id)]})
        assert created.status_code == 201
        spoofed = client.post("/api/v1/business-nodes/candidate-sets", headers={"Authorization": f"Bearer {attacker_token}"}, json={"owner_scope": owner.scoped_id, "candidate_version_ids": [str(artifact.artifact_version_id)]})
    assert spoofed.status_code == 403


def test_catalog_has_all_public_business_granularity_nodes():
    assert {item["type_id"] for item in BUSINESS_NODE_CATALOG} == {"brief", "constraint", "structured_generate", "model_router", "variants", "select_rank", "review", "transform", "workbench_task", "package_export"}


def test_business_executors_publish_typed_artifacts_and_candidate_evidence(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, run, _, brief_attempt = _run_context(factory, owner, "brief", {"brief": {"goal": "campaign", "audience": "readers"}})
    service = BusinessNodeService(factory)
    brief = service.execute_attempt(brief_attempt.attempt_id)
    assert brief[0].schema_id == "creative_brief"

    _, _, _, constraint_attempt = _run_context(factory, owner, "constraint", {"constraints": [{"format": "16:9"}, {"format": "9:16", "budget": 5}]})
    constraint = service.execute_attempt(constraint_attempt.attempt_id)
    assert constraint[0].content_json["conflicts"][0]["field"] == "format"

    _, variant_run, variant_node, variant_attempt = _run_context(factory, owner, "variants", {"candidate_payloads": [{"title": "A"}, {"title": "B"}], "failed_candidates": [{"index": 2, "code": "provider_failed"}], "cost_allocation": {"shared": 0.2}})
    candidates = service.execute_attempt(variant_attempt.attempt_id)
    assert len(candidates) == 3  # two candidate ArtifactVersions plus CandidateSet evidence
    # A partial provider failure does not discard successful variants.  The
    # persisted evidence keeps both machine code and human-readable reason,
    # and cost is attributed to the immutable candidate set.
    assert candidates[-1].content_json["failed_candidates"] == [
        {"index": 2, "code": "provider_failed", "reason": "provider_failed"}
    ]
    with factory() as session:
        row = session.scalar(text("SELECT candidate_set_id FROM candidate_sets WHERE run_id = :run AND node_run_id = :node"), {"run": variant_run.run_id, "node": variant_node.node_run_id})
        costs = session.scalar(text("SELECT cost_allocation FROM candidate_sets WHERE run_id = :run AND node_run_id = :node"), {"run": variant_run.run_id, "node": variant_node.node_run_id})
    assert row is not None
    assert costs == {"shared": 0.2}


def test_structured_generate_rejects_invalid_schema_before_artifact_publish(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, _, _, attempt = _run_context(factory, owner, "structured_generate", {"json_schema": {"type": "object", "required": ["title"]}, "output": {"wrong": True}})
    with pytest.raises(ValidationError_):
        BusinessNodeService(factory).execute_attempt(attempt.attempt_id)


def test_workbench_task_is_materialized_from_workflow_attempt(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    _, run, node, attempt = _run_context(factory, owner, "workbench_task", {"target_workbench": "shot-plan", "output_schema_ref": "shot_plan.v1", "resource_type": "shot_plan"})
    assert BusinessNodeService(factory).execute_attempt(attempt.attempt_id) == []
    with factory() as session:
        task = session.scalar(text("SELECT task_id FROM human_tasks WHERE run_id = :run AND node_run_id = :node"), {"run": run.run_id, "node": node.node_run_id})
    assert task is not None
