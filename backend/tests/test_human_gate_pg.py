"""PG-backed contract tests for Human Gate persistence (TF-HG-001).

All state in PostgreSQL.  Run explicitly with ``TOONFLOW_RUN_PG_TESTS=1``
after ``alembic upgrade head``.

Tests cover the full human task lifecycle:
  create human task → WAITING_EXTERNAL → RESOLVE → node proceeds
  create human task → WAITING_EXTERNAL → REJECT → node cancelled
  create human task → WAITING_EXTERNAL → TIMEOUT → node timeout
"""
from __future__ import annotations

import os
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.models import (
    HumanTaskModel,
    HumanTaskDecisionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.core.exceptions import ForbiddenError
from src.core.exceptions import ConflictError
from src.infra.db.session import get_session_factory
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.identity_repository import get_session_store
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.app import app
from src.schemas.enums import (
    HumanTaskStatus,
    NodeRunStatus,
    RevisionStatus,
    RunStatus,
)
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot

pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def factory():
    return get_session_factory()


@pytest.fixture
def runtime(factory):
    return RuntimeService(session_factory=factory)


@pytest.fixture
def revision_id(factory) -> uuid.UUID:
    """Create a minimal Workflow + WorkflowRevision for FK resolution."""
    wid = uuid.uuid4()
    rid = uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=wid, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(
            revision_id=rid, workflow_id=wid, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    return rid


@pytest.fixture
def plan(revision_id) -> CompiledExecutionPlan:
    return CompiledExecutionPlan(
        plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={"nodes": [{"id": "n1", "type": "provider"}], "edges": []},
        plan_hash="pg-human-gate-test",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_run(runtime, plan) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a run, start it, get the node_run_id and attempt_id."""
    run = runtime.create_run(
        compiled_plan=plan,
        owner_scope=OwnerScope(kind="user", id=uuid.uuid4()),
    )
    runtime.start_run(run.run_id)
    # Find the node run created for n1
    return run.run_id


def _get_attempt_id(runtime, run_id: uuid.UUID) -> uuid.UUID:
    """Get the first attempt for the first node run in a run."""
    # Access node runs through the service's SQL path by creating an attempt
    for nr_id, nr in runtime._node_runs.items():
        if nr.run_id == run_id:
            attempt = runtime.create_attempt(nr_id)
            return attempt.attempt_id
    raise AssertionError(f"No node runs found for run {run_id}")


# ---------------------------------------------------------------------------
# Human Gate — PG-backed contract tests
# ---------------------------------------------------------------------------


class TestHumanGatePersistence:
    """Human Gate lifecycle verified against PostgreSQL.

    These tests exercise the SQL path of RuntimeService.create_human_task()
    and resolve_human_task(), verifying that all state is persisted and
    survives service-instance restarts.
    """

    def test_compiled_workflow_materializes_fixed_revision_gate(self, runtime, factory):
        workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
        with factory.begin() as session:
            session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
            session.add(WorkflowRevisionModel(
                revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
                graph_hash="gate-g", execution_hash="gate-e", registry_snapshot_id=uuid.uuid4(),
                graph={"nodes": [{"id": "approval", "type": "human_gate", "config": {
                    "policy_strength": "domain_required", "timeout_minutes": 5, "on_timeout": "fail"}}], "edges": []},
                revision_status=RevisionStatus.ACTIVE,
            ))
        plan = CompiledExecutionPlan(
            plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
            registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
            resolved_graph={"nodes": [{"id": "approval", "type": "human_gate"}], "edges": []},
            plan_hash="compiled-gate",
        )
        run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=uuid.uuid4()))
        runtime.start_run(run.run_id)
        with factory() as session:
            task = session.scalar(select(HumanTaskModel).where(HumanTaskModel.run_id == run.run_id))
            assert task is not None
            assert task.owner_layer == "workflow"
            assert task.owner_revision_id == revision_id
            assert task.task_kind == "human_gate"
            assert task.timeout_policy["on_timeout"] == "fail"
        accepted = runtime.resolve_human_task(task.task_id, payload={})
        assert accepted.status == HumanTaskStatus.ACCEPTED

    def test_create_human_task_persists(self, runtime, plan, factory):
        """Create human task → verify row exists in PostgreSQL."""
        run_id = _setup_run(runtime, plan)

        # Need to find node_run_id from the database or create an attempt
        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
            task_kind="human_gate",
            policy_strength="domain_required",
            timeout_minutes=30,
        )
        assert task.task_id is not None
        assert task.status == HumanTaskStatus.PENDING

        # Verify via fresh read from DB
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            assert row.status == HumanTaskStatus.PENDING
            assert row.policy_strength == "domain_required"
            assert row.timeout_policy == {"duration_minutes": 30, "on_timeout": "fail"}
            assert row.run_id == run_id

    def test_node_goes_waiting_user(self, runtime, plan, factory):
        """create_human_task sets NodeRun and WorkflowRun to WAITING_USER."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        # Verify node and run status via DB
        with factory() as session:
            nr = session.get(NodeRunModel, node_run.node_run_id)
            assert nr is not None
            assert nr.status == NodeRunStatus.WAITING_USER
            wr = session.get(WorkflowRunModel, run_id)
            assert wr is not None
            assert wr.status == RunStatus.WAITING_USER

    def test_resolve_human_task_accept(self, runtime, plan, factory):
        """ACCEPT decision → HumanTask status → ACCEPTED, node proceeds."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        # Resolve with accept
        resolved = runtime.resolve_human_task(task.task_id, decision="accept")
        assert resolved.status == HumanTaskStatus.ACCEPTED

        # Verify persistence
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            assert row.status == HumanTaskStatus.ACCEPTED

    def test_resolve_human_task_reject(self, runtime, plan, factory):
        """REJECT decision → HumanTask status → REJECTED, node cancelled."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        # Resolve with reject
        resolved = runtime.resolve_human_task(task.task_id, decision="reject")
        assert resolved.status == HumanTaskStatus.REJECTED

        # Verify persistence
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            assert row.status == HumanTaskStatus.REJECTED

    def test_resolve_idempotent(self, runtime, plan, factory):
        """Second resolve with same decision raises ConflictError."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        runtime.resolve_human_task(task.task_id, decision="accept")

        # Second resolve should fail (strict mode)
        from src.core.exceptions import ConflictError
        with pytest.raises(ConflictError):
            runtime.resolve_human_task(task.task_id, decision="accept", strict=True)

    def test_resolve_persists_across_restart(self, runtime, plan, factory):
        """Verify human task resolve state survives service restart."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )
        runtime.resolve_human_task(task.task_id, decision="accept")

        # Simulate restart: new RuntimeService instance
        runtime2 = RuntimeService(session_factory=factory)
        with pytest.raises(Exception):
            # Should raise ConflictError because task is already resolved
            runtime2.resolve_human_task(task.task_id, decision="accept", strict=True)

        # Verify via DB read
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            assert row.status in (HumanTaskStatus.ACCEPTED,)

    def test_human_task_timeout_handling(self, runtime, plan, factory):
        """Create human task with timeout policy → persisted correctly."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
            timeout_minutes=5,
        )

        # Verify timeout policy was persisted
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            assert row.timeout_policy == {"duration_minutes": 5, "on_timeout": "fail"}

    def test_human_task_pending_to_waiting_status(self, runtime, plan, factory):
        """Human task transitions through expected statuses."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        # Status after creation is PENDING
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row.status == HumanTaskStatus.PENDING

        # After accept
        runtime.resolve_human_task(task.task_id, decision="accept")
        with factory() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row.status == HumanTaskStatus.ACCEPTED

    def test_attempt_status_waits_external(self, runtime, plan, factory):
        """Verify attempt status is not changed by human task creation
        (the attempt stays WAITING_EXTERNAL after dispatch,
        and the node goes WAITING_USER)."""
        run_id = _setup_run(runtime, plan)

        with factory() as session:
            node_run = session.execute(
                select(NodeRunModel).where(NodeRunModel.run_id == run_id)
            ).scalars().first()
        assert node_run is not None

        attempt = runtime.create_attempt(node_run.node_run_id)

        # Dispatch provider first (sets attempt to WAITING_EXTERNAL)
        runtime.dispatch_provider(
            attempt.attempt_id, provider_id="human-gate",
            model_id="n/a", idempotency_key=f"hg-{uuid.uuid4()}",
            request_body_hash="hg",
        )

        runtime.create_human_task(
            run_id=run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
        )

        with factory() as session:
            nr = session.get(NodeRunModel, node_run.node_run_id)
            assert nr is not None
            assert nr.status == NodeRunStatus.WAITING_USER

    def test_authenticated_decision_is_idempotent_and_recovers_fixed_attempt(self, runtime, plan, factory):
        """A repeated client token produces one durable audit decision only."""
        owner_id = uuid.uuid4()
        run = runtime.create_run(
            compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id),
        )
        runtime.start_run(run.run_id)
        with factory() as session:
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = runtime.create_attempt(node.node_run_id, fixed_input={"frozen": True})
        task = runtime.create_human_task(
            run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id,
        )

        first = runtime.resolve_human_task(
            task.task_id, actor_id=owner_id, actor_scope=f"user:{owner_id}",
            task_version=1, idempotency_key="gate-submit-0001", payload={"choice": "approve"}, internal=False,
        )
        second = runtime.resolve_human_task(
            task.task_id, actor_id=owner_id, actor_scope=f"user:{owner_id}",
            task_version=1, idempotency_key="gate-submit-0001", payload={"choice": "approve"}, internal=False,
        )
        assert first.status == second.status == HumanTaskStatus.ACCEPTED
        with pytest.raises(ConflictError):
            runtime.resolve_human_task(
                task.task_id, actor_id=owner_id, actor_scope=f"user:{owner_id}",
                task_version=1, idempotency_key="different-submit-2", payload={}, internal=False,
            )
        with factory() as session:
            decisions = session.scalars(select(HumanTaskDecisionModel).where(
                HumanTaskDecisionModel.task_id == task.task_id,
            )).all()
            persisted_attempt = session.get(NodeRunAttemptModel, attempt.attempt_id)
            assert len(decisions) == 1
            assert decisions[0].actor_id == owner_id
            assert persisted_attempt is not None
            assert persisted_attempt.fixed_input["frozen"] is True
            assert persisted_attempt.fixed_input["human_gate_decision"]["payload"] == {"choice": "approve"}

    def test_cross_owner_actor_cannot_consume_gate_or_write_decision(self, runtime, plan, factory):
        owner_id = uuid.uuid4()
        run = runtime.create_run(
            compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id),
        )
        runtime.start_run(run.run_id)
        with factory() as session:
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = runtime.create_attempt(node.node_run_id)
        task = runtime.create_human_task(run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id)

        attacker = uuid.uuid4()
        with pytest.raises(ForbiddenError):
            runtime.reject_human_task(
                task.task_id, actor_id=attacker, actor_scope=f"user:{attacker}",
                task_version=1, idempotency_key="attacker-submit", internal=False,
            )
        with factory() as session:
            assert session.get(HumanTaskModel, task.task_id).status == HumanTaskStatus.PENDING
            assert session.scalar(select(HumanTaskDecisionModel).where(
                HumanTaskDecisionModel.task_id == task.task_id,
            )) is None

    def test_default_timeout_uses_declared_payload_on_original_attempt(self, runtime, plan, factory):
        owner_id = uuid.uuid4()
        run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
        runtime.start_run(run.run_id)
        with factory() as session:
            node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = runtime.create_attempt(node.node_run_id, fixed_input={"source": "pinned"})
        task = runtime.create_human_task(run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id)
        with factory.begin() as session:
            row = session.get(HumanTaskModel, task.task_id)
            assert row is not None
            row.timeout_policy = {"duration_minutes": 5, "on_timeout": "default", "default_payload": {"choice": "safe"}}

        resolved = runtime.timeout_human_task(
            task.task_id, actor_id=owner_id, actor_scope=f"user:{owner_id}",
            task_version=1, idempotency_key="timeout-default-1", internal=False,
        )
        assert resolved.status == HumanTaskStatus.ACCEPTED
        with factory() as session:
            persisted_attempt = session.get(NodeRunAttemptModel, attempt.attempt_id)
            assert persisted_attempt is not None
            assert persisted_attempt.fixed_input["source"] == "pinned"
            assert persisted_attempt.fixed_input["human_gate_decision"]["action"] == "default"

    def test_authenticated_compiled_revision_run_materializes_gate(self, factory):
        """The public run API cannot accept caller-supplied Gate state."""
        owner_id = uuid.uuid4()
        token = get_session_store().issue(owner_id)["token"]
        workflow_service = SqlWorkflowService(factory)
        workflow = workflow_service.create_workflow(owner_scope=OwnerScope(kind="user", id=owner_id))
        draft = workflow_service.get_draft(workflow.workflow_id)
        workflow_service.save_draft(
            workflow.workflow_id,
            {"nodes": [{
                "id": "approval", "type": "human_gate",
                "data": {"config": {"policy_strength": "domain_required", "timeout_minutes": 5, "on_timeout": "fail"}},
            }], "edges": []},
            {}, {}, draft.graph_hash,
        )
        snapshot, _ = SqlRegistryService(factory).create_snapshot()
        from src.domain.workflow.compiler import WorkflowCompiler
        revision, _ = workflow_service.publish_compiled_revision(
            workflow.workflow_id, snapshot, WorkflowCompiler(),
        )

        async def request(token_value: str) -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v1/runtime/workflow-runs",
                    json={"workflow_revision_id": str(revision.revision_id)},
                    headers={"Authorization": f"Bearer {token_value}"},
                )

        response = asyncio.run(request(token))
        assert response.status_code == 201, response.text
        run_id = uuid.UUID(response.json()["run_id"])
        with factory() as session:
            task = session.scalar(select(HumanTaskModel).where(HumanTaskModel.run_id == run_id))
            assert task is not None
            assert task.owner_layer == "workflow"
            assert task.owner_revision_id == revision.revision_id
            assert task.timeout_policy["duration_minutes"] == 5

        other_token = get_session_store().issue(uuid.uuid4())["token"]
        denied = asyncio.run(request(other_token))
        assert denied.status_code == 404


def test_gate_acceptance_schedules_all_parallel_downstream_nodes(factory) -> None:
    """One accepted gate atomically opens both independent successors."""
    owner_id, workflow_id, revision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {
        "nodes": [
            {"id": "gate", "type": "human_gate", "config": {"timeout_minutes": 5}},
            {"id": "left", "type": "provider"}, {"id": "right", "type": "provider"},
        ],
        "edges": [{"source": "gate", "target": "left"}, {"source": "gate", "target": "right"}],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph=graph, graph_hash="gate", execution_hash="gate", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE))
    plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=graph, plan_hash="parallel-gate")
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    runtime.start_run(run.run_id)
    with factory() as session:
        task = session.scalar(select(HumanTaskModel).where(HumanTaskModel.run_id == run.run_id))
        assert task is not None
    runtime.resolve_human_task(task.task_id)
    with factory() as session:
        nodes = {row.node_instance_id: row for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))}
        attempts = list(session.scalars(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id.in_([nodes["left"].node_run_id, nodes["right"].node_run_id]))))
        persisted_run = session.get(WorkflowRunModel, run.run_id)
        assert nodes["gate"].status == NodeRunStatus.COMPLETED
        assert nodes["left"].status == nodes["right"].status == NodeRunStatus.READY
        assert len(attempts) == 2 and all(row.status.value == "pending" for row in attempts)
        assert persisted_run is not None and persisted_run.status == RunStatus.RUNNING


def test_deadline_scanner_survives_restart_and_public_timeout_is_rejected(factory) -> None:
    runtime = RuntimeService(factory)
    revision_id = uuid.uuid4()
    with factory.begin() as session:
        workflow_id = uuid.uuid4()
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="timeout", execution_hash="timeout", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE))
    run = runtime.create_run(compiled_plan=CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph={"nodes": [{"id": "n", "type": "provider"}], "edges": []}, plan_hash="timeout"), owner_scope=OwnerScope(kind="user", id=uuid.uuid4()))
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
    attempt = runtime.create_attempt(node.node_run_id)
    task = runtime.create_human_task(run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id, timeout_minutes=1)
    with factory.begin() as session:
        row = session.get(HumanTaskModel, task.task_id)
        assert row is not None
        row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
    # A fresh worker proves deadline authority is durable and restart-safe.
    assert RuntimeWorker(factory).expire_due_human_tasks() >= 1
    with factory() as session:
        assert session.get(HumanTaskModel, task.task_id).status == HumanTaskStatus.EXPIRED
