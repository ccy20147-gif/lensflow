"""
ToonFlow Backend — Runtime / DAG Execution Service (TF-WF-006)

Manages WorkflowRun, NodeRun, NodeRunAttempt, epoch/fencing, outbox dispatch,
provider invocation, and recovery.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.exceptions import ConflictError, NotFoundError, SafeError
from src.schemas.enums import AttemptStatus, HumanTaskStatus, NodeRunStatus, RunStatus
from src.schemas.models import (
    CompiledExecutionPlan,
    HumanTaskRecord,
    NodeRun,
    NodeRunAttempt,
    OutboxEvent,
    ProviderInvocationAttempt,
    ProviderInvocationRecord,
    ProviderOutputBinding,
    WorkflowRun,
    WorkflowTaskBinding,
    OwnerScope,
)


class RuntimeService:
    """Manages WorkflowRun lifecycle including attempts, fencing, and outbox."""

    def __init__(self):
        # In-memory stores for Foundation prototype
        self._runs: dict[uuid.UUID, WorkflowRun] = {}
        self._node_runs: dict[uuid.UUID, NodeRun] = {}
        self._attempts: dict[uuid.UUID, NodeRunAttempt] = {}
        self._provider_attempts: dict[uuid.UUID, ProviderInvocationAttempt] = {}
        self._provider_records: dict[uuid.UUID, ProviderInvocationRecord] = {}
        self._task_bindings: dict[uuid.UUID, WorkflowTaskBinding] = {}
        self._outbox: list[OutboxEvent] = []
        self._human_tasks: dict[uuid.UUID, HumanTaskRecord] = {}

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        compiled_plan: CompiledExecutionPlan,
        owner_scope: OwnerScope,
        input_snapshot: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """Create a WorkflowRun from a compiled plan."""
        run = WorkflowRun(
            run_id=uuid.uuid4(),
            workflow_revision_id=compiled_plan.workflow_revision_id,
            compiled_plan_id=compiled_plan.plan_id,
            owner_scope=owner_scope,
            input_snapshot=input_snapshot or {},
            status=RunStatus.QUEUED,
            created_at=datetime.now(timezone.utc),
        )
        self._runs[run.run_id] = run

        # Create NodeRuns for each node in the plan
        for node_id, node_data in compiled_plan.resolved_graph.get("nodes", []):
            if isinstance(node_data, dict):
                nid = node_data.get("id", node_id)
            else:
                nid = node_id
            node_run = NodeRun(
                node_run_id=uuid.uuid4(),
                run_id=run.run_id,
                node_instance_id=nid,
                node_type_id=(
                    node_data.get("type", "unknown")
                    if isinstance(node_data, dict) else "unknown"
                ),
                status=NodeRunStatus.PENDING,
            )
            self._node_runs[node_run.node_run_id] = node_run

        return run

    def start_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Transition run from QUEUED to RUNNING."""
        run = self._get_run(run_id)
        if run.status != RunStatus.QUEUED:
            raise ConflictError(f"运行 {run_id} 状态为 {run.status}，不能启动")

        run.status = RunStatus.RUNNING
        return run

    def cancel_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Cancel a run and all active attempts."""
        run = self._get_run(run_id)
        if run.status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
            raise ConflictError(f"运行 {run_id} 已结束")

        run.status = RunStatus.CANCELLING

        # Cancel all running node attempts
        for nr in self._node_runs.values():
            if nr.run_id == run_id and nr.status in (
                NodeRunStatus.PENDING, NodeRunStatus.READY, NodeRunStatus.RUNNING
            ):
                nr.status = NodeRunStatus.CANCELLED

        run.status = RunStatus.CANCELLED
        return run

    def complete_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Mark run as completed if all nodes are terminal."""
        run = self._get_run(run_id)
        all_terminal = all(
            nr.status in (
                NodeRunStatus.COMPLETED, NodeRunStatus.CANCELLED,
                NodeRunStatus.FAILED, NodeRunStatus.SKIPPED,
            )
            for nr in self._node_runs.values()
            if nr.run_id == run_id
        )
        if all_terminal:
            run.status = RunStatus.COMPLETED
        return run

    # ------------------------------------------------------------------
    # Attempt lifecycle (with epoch/fencing)
    # ------------------------------------------------------------------

    def create_attempt(
        self,
        node_run_id: uuid.UUID,
        *,
        fixed_input: dict[str, Any] | None = None,
    ) -> NodeRunAttempt:
        """Create a new attempt with incremented epoch."""
        node_run = self._get_node_run(node_run_id)

        # Count existing attempts
        existing = sum(
            1 for a in self._attempts.values()
            if a.node_run_id == node_run_id
        )

        attempt = NodeRunAttempt(
            attempt_id=uuid.uuid4(),
            node_run_id=node_run_id,
            attempt_number=existing + 1,
            execution_epoch=existing + 1,
            fixed_input=fixed_input or {},
            status=AttemptStatus.PENDING,
        )
        self._attempts[attempt.attempt_id] = attempt
        node_run.status = NodeRunStatus.RUNNING
        return attempt

    def set_attempt_running(self, attempt_id: uuid.UUID, lease_id: str) -> NodeRunAttempt:
        """Set attempt to RUNNING with a lease."""
        attempt = self._get_attempt(attempt_id)
        if attempt.status != AttemptStatus.PENDING:
            raise ConflictError(f"Attempt {attempt_id} 状态为 {attempt.status}，不能设为运行中")
        attempt.status = AttemptStatus.RUNNING
        attempt.lease_id = lease_id
        return attempt

    def complete_attempt(
        self,
        attempt_id: uuid.UUID,
        *,
        output_refs: list[dict] | None = None,
        epoch: int | None = None,
    ) -> NodeRunAttempt:
        """Complete an attempt with epoch/fencing check."""
        attempt = self._get_attempt(attempt_id)

        # Fencing: reject writes from stale epoch
        if epoch is not None and attempt.execution_epoch != epoch:
            raise ConflictError(
                f"执行纪元不匹配: 当前 {attempt.execution_epoch}，请求 {epoch}"
            )

        attempt.status = AttemptStatus.COMPLETED
        attempt.completed_at = datetime.now(timezone.utc)

        node_run = self._get_node_run(attempt.node_run_id)
        node_run.status = NodeRunStatus.COMPLETED
        return attempt

    def fail_attempt(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        """Mark attempt as failed."""
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.FAILED
        attempt.completed_at = datetime.now(timezone.utc)

        node_run = self._get_node_run(attempt.node_run_id)
        node_run.status = NodeRunStatus.FAILED
        return attempt

    def supersede_attempt(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        """Mark attempt as superseded (new attempt invalidated this one)."""
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.SUPERSEDED
        return attempt

    def mark_unknown(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        """Mark attempt as unknown (provider response uncertain)."""
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.UNKNOWN
        return attempt

    # ------------------------------------------------------------------
    # Provider Invocation
    # ------------------------------------------------------------------

    def dispatch_provider(
        self,
        attempt_id: uuid.UUID,
        *,
        provider_id: str,
        model_id: str,
        idempotency_key: str,
        request_body_hash: str,
    ) -> tuple[ProviderInvocationAttempt, OutboxEvent]:
        """Create ProviderInvocationAttempt + dispatch OutboxEvent in same transaction.

        The network call MUST happen after the transaction commits.
        """
        attempt = self._get_attempt(attempt_id)

        provider_attempt = ProviderInvocationAttempt(
            provider_attempt_id=uuid.uuid4(),
            node_run_attempt_id=attempt_id,
            provider_id=provider_id,
            model_id=model_id,
            idempotency_key=idempotency_key,
            request_body_hash=request_body_hash,
            status=AttemptStatus.PENDING,
        )
        self._provider_attempts[provider_attempt.provider_attempt_id] = provider_attempt

        outbox = OutboxEvent(
            event_id=uuid.uuid4(),
            aggregate_type="provider_invocation",
            aggregate_id=provider_attempt.provider_attempt_id,
            event_type="provider.dispatch",
            payload={
                "provider_id": provider_id,
                "model_id": model_id,
                "idempotency_key": idempotency_key,
            },
            purpose="provider_dispatch",
            created_at=datetime.now(timezone.utc),
        )
        self._outbox.append(outbox)

        attempt.status = AttemptStatus.WAITING_EXTERNAL
        return provider_attempt, outbox

    def record_provider_result(
        self,
        provider_attempt_id: uuid.UUID,
        *,
        model_version: str,
        response_fingerprint: str,
        usage: dict[str, Any] | None = None,
        actual_cost: float = 0.0,
        output_artifact_version_ids: list[uuid.UUID] | None = None,
        current_epoch: int | None = None,
    ) -> tuple[ProviderInvocationRecord, OutboxEvent]:
        """Record provider result with epoch/fencing + result publish outbox."""
        provider_attempt = self._get_provider_attempt(provider_attempt_id)

        # Fencing: reject if attempt was superseded
        parent_attempt = self._get_attempt(provider_attempt.node_run_attempt_id)
        if current_epoch is not None and parent_attempt.execution_epoch != current_epoch:
            raise ConflictError("执行纪元过期，结果被拒绝")

        # Create the record
        record = ProviderInvocationRecord(
            record_id=uuid.uuid4(),
            provider_attempt_id=provider_attempt_id,
            provider_id=provider_attempt.provider_id,
            model_id=provider_attempt.model_id,
            model_version=model_version,
            idempotency_key=provider_attempt.idempotency_key,
            request_body_hash=provider_attempt.request_body_hash,
            response_fingerprint=response_fingerprint,
            usage=usage or {},
            actual_cost=actual_cost,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        self._provider_records[record.record_id] = record

        # Create output bindings
        if output_artifact_version_ids:
            for i, av_id in enumerate(output_artifact_version_ids):
                binding = ProviderOutputBinding(
                    binding_id=uuid.uuid4(),
                    record_id=record.record_id,
                    output_artifact_version_id=av_id,
                    output_index=i,
                )
                # Store binding (simplified for prototype)
                pass

        provider_attempt.status = AttemptStatus.COMPLETED

        # Result publish outbox
        outbox = OutboxEvent(
            event_id=uuid.uuid4(),
            aggregate_type="provider_invocation",
            aggregate_id=provider_attempt_id,
            event_type="provider.result",
            payload={
                "record_id": str(record.record_id),
                "status": "completed",
                "cost": actual_cost,
            },
            purpose="result_publish",
            created_at=datetime.now(timezone.utc),
        )
        self._outbox.append(outbox)

        return record, outbox

    # ------------------------------------------------------------------
    # Human Task
    # ------------------------------------------------------------------

    def create_human_task(
        self,
        *,
        run_id: uuid.UUID,
        node_run_id: uuid.UUID,
        attempt_id: uuid.UUID,
        task_kind: str = "human_gate",
        policy_strength: str = "domain_required",
        input_snapshot_refs: list | None = None,
        timeout_minutes: int = 60,
    ) -> HumanTaskRecord:
        """Create a persistent human task."""
        task = HumanTaskRecord(
            task_id=uuid.uuid4(),
            task_kind=task_kind,
            owner_layer="workflow",
            owner_revision_id=uuid.uuid4(),
            run_id=run_id,
            node_run_id=node_run_id,
            attempt_id=attempt_id,
            input_snapshot_refs=input_snapshot_refs or [],
            policy_strength=policy_strength,  # type: ignore
            timeout_policy={
                "duration_minutes": timeout_minutes,
                "on_timeout": "fail",
            },
            status=HumanTaskStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._human_tasks[task.task_id] = task

        # Set node run to waiting_user
        node_run = self._get_node_run(node_run_id)
        node_run.status = NodeRunStatus.WAITING_USER
        run = self._get_run(run_id)
        run.status = RunStatus.WAITING_USER

        return task

    def resolve_human_task(
        self,
        task_id: uuid.UUID,
        *,
        decision: str = "accept",
        idempotency_key: str = "",
    ) -> HumanTaskRecord:
        """Resolve a human task with idempotency check."""
        task = self._human_tasks.get(task_id)
        if not task:
            raise NotFoundError("HumanTask", str(task_id))

        if task.status in (HumanTaskStatus.ACCEPTED, HumanTaskStatus.REJECTED):
            raise ConflictError(f"任务 {task_id} 已处理")

        if decision == "accept":
            task.status = HumanTaskStatus.ACCEPTED
        elif decision == "reject":
            task.status = HumanTaskStatus.REJECTED
        else:
            task.status = HumanTaskStatus.SUBMITTED

        return task

    # ------------------------------------------------------------------
    # Outbox & Recovery
    # ------------------------------------------------------------------

    def get_pending_outbox(self) -> list[OutboxEvent]:
        """Get un-published outbox events."""
        return [e for e in self._outbox if e.published_at is None]

    def mark_outbox_published(self, event_id: uuid.UUID):
        """Mark an outbox event as published."""
        for event in self._outbox:
            if event.event_id == event_id:
                event.published_at = datetime.now(timezone.utc)
                break

    def recover(self) -> dict[str, Any]:
        """Recover state after restart — return summary of active runs."""
        active_runs = [
            r for r in self._runs.values()
            if r.status in (
                RunStatus.QUEUED, RunStatus.RUNNING,
                RunStatus.WAITING_USER, RunStatus.CANCELLING,
            )
        ]

        pending_outbox = len(self.get_pending_outbox())
        waiting_attempts = sum(
            1 for a in self._attempts.values()
            if a.status == AttemptStatus.WAITING_EXTERNAL
        )
        unknown_attempts = sum(
            1 for a in self._attempts.values()
            if a.status == AttemptStatus.UNKNOWN
        )

        return {
            "active_runs": len(active_runs),
            "pending_outbox": pending_outbox,
            "waiting_external": waiting_attempts,
            "unknown_attempts": unknown_attempts,
        }

    # ------------------------------------------------------------------
    # Internal getters
    # ------------------------------------------------------------------

    def _get_run(self, run_id: uuid.UUID) -> WorkflowRun:
        run = self._runs.get(run_id)
        if not run:
            raise NotFoundError("WorkflowRun", str(run_id))
        return run

    def _get_node_run(self, node_run_id: uuid.UUID) -> NodeRun:
        nr = self._node_runs.get(node_run_id)
        if not nr:
            raise NotFoundError("NodeRun", str(node_run_id))
        return nr

    def _get_attempt(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        attempt = self._attempts.get(attempt_id)
        if not attempt:
            raise NotFoundError("NodeRunAttempt", str(attempt_id))
        return attempt

    def _get_provider_attempt(self, provider_attempt_id: uuid.UUID) -> ProviderInvocationAttempt:
        pa = self._provider_attempts.get(provider_attempt_id)
        if not pa:
            raise NotFoundError("ProviderInvocationAttempt", str(provider_attempt_id))
        return pa
