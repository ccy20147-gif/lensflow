"""Durable Agent RequestInput contracts (TF-WF-008)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.core.exceptions import ValidationError_
from src.domain.agent.request_input import AgentRequestInputService
from src.domain.runtime.runtime_service import RuntimeService
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.models import (
    ArtifactVersionModel,
    HumanTaskDecisionModel,
    HumanTaskModel,
    NodeRunAttemptModel,
    NodeRunModel,
    WorkflowModel,
    WorkflowRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import HumanTaskStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


def _setup() -> tuple[AgentRequestInputService, RuntimeService, Any, OwnerScope, Any, Any, tuple[Any, Any]]:
    factory = get_session_factory()
    owner_id, workflow_id, revision_id = uuid4(), uuid4(), uuid4()
    owner = OwnerScope(kind="user", id=owner_id)
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    plan = CompiledExecutionPlan(
        plan_id=uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()),
        resolved_graph={"nodes": [{"id": "agent", "type": "agent_invoke"}], "edges": []}, plan_hash="request-input",
    )
    run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert attempt is not None
    repo = SqlAgentRepository(factory)
    agent = repo.create_definition(name="request-input", description="", agent_kind="configurable", owner_scope=owner.scoped_id)
    revision = repo.create_revision(agent.agent_id, {
        "sop_steps": [{"step_id": "ask", "instruction": "ask user"}],
        "execution_policy": {"provider_ref": "atlascloud/test"},
    })
    repo.promote_revision(revision.revision_id)
    return AgentRequestInputService(factory), runtime, factory, owner, revision, run, (node, attempt)


def test_request_input_persists_typed_answer_decision_and_idempotent_retry() -> None:
    service, _runtime, factory, owner, revision, run, pair = _setup()
    node, attempt = pair
    task = service.create(
        agent_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id,
        attempt_id=attempt.attempt_id, schema_ref="choice@1", question="Choose", timeout_minutes=10,
        idempotency_token="create-token", requester_scope=owner.scoped_id,
        input_schema={"type": "object", "required": ["choice"], "properties": {"choice": {"type": "string", "enum": ["yes", "no"]}}},
    )
    # A fresh service instance proves the task survives a process/browser refresh.
    recovered = AgentRequestInputService(factory)
    with pytest.raises(ValidationError_):
        recovered.resolve(task_id=task.task_id, task_version=1, idempotency_token="submit-token", answer={"choice": "bad"}, requester_scope=owner.scoped_id)
    accepted = recovered.resolve(task_id=task.task_id, task_version=1, idempotency_token="submit-token", answer={"choice": "yes"}, requester_scope=owner.scoped_id)
    retried = recovered.resolve(task_id=task.task_id, task_version=1, idempotency_token="submit-token", answer={"choice": "yes"}, requester_scope=owner.scoped_id)
    assert accepted.status == HumanTaskStatus.ACCEPTED and retried.status == HumanTaskStatus.ACCEPTED
    with factory() as session:
        decisions = list(session.scalars(select(HumanTaskDecisionModel).where(HumanTaskDecisionModel.task_id == task.task_id)))
        persisted_attempt = session.get(NodeRunAttemptModel, attempt.attempt_id)
        traces = list(session.scalars(select(ArtifactVersionModel).where(
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
            ArtifactVersionModel.created_by_run_id == run.run_id,
        )))
        assert len(decisions) == 1 and decisions[0].typed_payload == {"choice": "yes"}
        assert persisted_attempt is not None and persisted_attempt.fixed_input["request_input"]["answer"] == {"choice": "yes"}
        assert {row.content_json["phase"] for row in traces} == {"waiting_user", "resumed"}
        assert all("answer" not in row.content_json for row in traces)


def test_request_input_deadline_fails_original_run_and_rejects_late_answer() -> None:
    service, runtime, factory, owner, revision, run, pair = _setup()
    node, attempt = pair
    task = service.create(
        agent_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id,
        attempt_id=attempt.attempt_id, schema_ref="text@1", question="Explain", timeout_minutes=1,
        idempotency_token="create-timeout", requester_scope=owner.scoped_id,
        input_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
    )
    with factory.begin() as session:
        row = session.get(HumanTaskModel, task.task_id)
        assert row is not None
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
    assert runtime.expire_due_human_tasks() >= 1
    with factory() as session:
        persisted_task = session.get(HumanTaskModel, task.task_id)
        persisted_node = session.get(NodeRunModel, node.node_run_id)
        from src.infra.db.models import WorkflowRunModel
        persisted_run = session.get(WorkflowRunModel, run.run_id)
        assert persisted_task is not None and persisted_task.status == HumanTaskStatus.EXPIRED
        assert persisted_node is not None and persisted_node.status == NodeRunStatus.FAILED
        assert persisted_run is not None and persisted_run.status == RunStatus.FAILED
    with pytest.raises(Exception):
        service.resolve(task_id=task.task_id, task_version=1, idempotency_token="late-answer", answer={"text": "late"}, requester_scope=owner.scoped_id)
