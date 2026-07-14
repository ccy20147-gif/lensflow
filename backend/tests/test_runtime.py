"""
ToonFlow Backend — Runtime Tests (TF-WF-006)
"""
from __future__ import annotations

import uuid
import pytest

from src.domain.runtime.runtime_service import RuntimeService
from src.schemas.enums import AttemptStatus, NodeRunStatus, RunStatus, HumanTaskStatus
from src.schemas.models import (
    CompiledExecutionPlan,
    OwnerScope,
    RegistrySnapshot,
)


@pytest.fixture
def runtime():
    return RuntimeService()


@pytest.fixture
def owner():
    return OwnerScope(kind="user", id=uuid.uuid4())


@pytest.fixture
def plan():
    return CompiledExecutionPlan(
        plan_id=uuid.uuid4(),
        workflow_revision_id=uuid.uuid4(),
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={
            "nodes": [
                {"id": "n1", "type": "brief"},
                {"id": "n2", "type": "generate"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "in"},
            ],
        },
        plan_hash="test_hash_123",
    )


class TestRuntime:
    """FR-1 to FR-18 from TF-WF-006"""

    def test_create_run(self, runtime, plan, owner):
        """FR-1: WorkflowRun must fix revision and plan"""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        assert run.run_id is not None
        assert run.workflow_revision_id == plan.workflow_revision_id
        assert run.compiled_plan_id == plan.plan_id
        assert run.status == RunStatus.QUEUED

    def test_run_lifecycle(self, runtime, plan, owner):
        """Normal: QUEUED -> RUNNING -> COMPLETED"""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        run = runtime.start_run(run.run_id)
        assert run.status == RunStatus.RUNNING

        # Complete all node runs
        for nr in list(runtime._node_runs.values()):
            if nr.run_id == run.run_id:
                nr.status = NodeRunStatus.COMPLETED

        # Now complete_run should see all terminal
        run = runtime.complete_run(run.run_id)
        assert run.status == RunStatus.COMPLETED

    def test_cancel_run(self, runtime, plan, owner):
        """FR-5 + FR-11: Cancel stops new scheduling"""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        run = runtime.cancel_run(run.run_id)
        assert run.status == RunStatus.CANCELLED

    def test_create_attempt(self, runtime, plan, owner):
        """FR-2 + FR-3: Attempt with execution_epoch"""
        runtime.create_run(compiled_plan=plan, owner_scope=owner)
        # Get first node run
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        assert attempt.attempt_number == 1
        assert attempt.execution_epoch == 1
        assert attempt.status == AttemptStatus.PENDING

    def test_supersede_attempt(self, runtime, plan, owner):
        """FR-5: New attempt must invalidate old epoch"""
        runtime.create_run(compiled_plan=plan, owner_scope=owner)
        node_run = list(runtime._node_runs.values())[0]

        attempt1 = runtime.create_attempt(node_run.node_run_id)
        superseded = runtime.supersede_attempt(attempt1.attempt_id)
        assert superseded.status == AttemptStatus.SUPERSEDED

    def test_epoch_fencing_rejects_stale(self, runtime, plan, owner):
        """FR-4: Result publish must check execution_epoch"""
        runtime.create_run(compiled_plan=plan, owner_scope=owner)
        node_run = list(runtime._node_runs.values())[0]

        attempt = runtime.create_attempt(node_run.node_run_id)
        # Try to complete with wrong epoch
        with pytest.raises(Exception) as exc:
            runtime.complete_attempt(attempt.attempt_id, epoch=99)
        assert "纪元" in str(exc.value) or "epoch" in str(exc.value).lower()

    def test_provider_dispatch_outbox(self, runtime, plan, owner):
        """FR-6 + FR-14: Provider dispatch writes outbox in same transaction"""
        runtime.create_run(compiled_plan=plan, owner_scope=owner)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)

        provider_attempt, outbox = runtime.dispatch_provider(
            attempt.attempt_id,
            provider_id="test-provider",
            model_id="test-model",
            idempotency_key="test-idem-001",
            request_body_hash="abc123",
        )
        assert provider_attempt.provider_attempt_id is not None
        assert outbox.purpose == "provider_dispatch"
        assert outbox.published_at is None

        # Verify outbox is pending
        pending = runtime.get_pending_outbox()
        assert len(pending) > 0

    def test_provider_result_with_fencing(self, runtime, plan, owner):
        """FR-17: Result publish checks epoch"""
        runtime.create_run(compiled_plan=plan, owner_scope=owner)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id,
            provider_id="test-provider",
            model_id="test-model",
            idempotency_key="test-idem-002",
            request_body_hash="abc123",
        )

        rec, outbox = runtime.record_provider_result(
            provider_attempt.provider_attempt_id,
            model_version="1.0",
            response_fingerprint="resp123",
            usage={"tokens": 100},
            actual_cost=0.05,
            output_artifact_version_ids=[uuid.uuid4()],
            current_epoch=attempt.execution_epoch,
        )
        assert rec.provider_attempt_id == provider_attempt.provider_attempt_id
        assert outbox.purpose == "result_publish"

    def test_human_task_waits(self, runtime, plan, owner):
        """FR-Human task: waiting_user state"""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)

        task = runtime.create_human_task(
            run_id=run.run_id,
            node_run_id=node_run.node_run_id,
            attempt_id=attempt.attempt_id,
            task_kind="human_gate",
        )
        assert task.status == HumanTaskStatus.PENDING
        assert node_run.status == NodeRunStatus.WAITING_USER
        assert run.status == RunStatus.WAITING_USER

    def test_run_recovery(self, runtime, plan, owner):
        """FR-9: Service restart recovers active runs"""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)

        summary = runtime.recover()
        assert summary["active_runs"] >= 1
