"""
ToonFlow Backend — Runtime / DAG Execution Service (TF-WF-006)

Manages WorkflowRun, NodeRun, NodeRunAttempt, epoch/fencing, outbox dispatch,
provider invocation, and recovery.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
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
from src.infra.db.models import (
    ArtifactVersionModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
    CompiledExecutionPlanModel,
    HumanTaskModel,
    HumanTaskDecisionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    OutboxEventModel,
    ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    WorkflowTaskBindingModel,
    WorkflowRunModel,
    WorkflowRevisionModel,
    WorkflowModel,
    ConditionModel,
    JoinModel,
    ForEachRunModel,
    MapItemRunModel,
    SubworkflowModel,
)


class RuntimeService:
    """Manages WorkflowRun lifecycle including attempts, fencing, and outbox."""

    def __init__(self, session_factory: Any | None = None):
        """Use ``session_factory`` for durable execution state; omit for local unit tests.

        A provider network call is deliberately not made here.  It is consumed from
        the committed outbox by a worker, so dispatch/result state cannot be split
        across a network failure and a database failure.
        """
        self._session_factory = session_factory
        # In-memory stores for Foundation prototype
        self._runs: dict[uuid.UUID, WorkflowRun] = {}
        self._node_runs: dict[uuid.UUID, NodeRun] = {}
        self._attempts: dict[uuid.UUID, NodeRunAttempt] = {}
        self._provider_attempts: dict[uuid.UUID, ProviderInvocationAttempt] = {}
        self._provider_records: dict[uuid.UUID, ProviderInvocationRecord] = {}
        self._output_bindings: dict[uuid.UUID, ProviderOutputBinding] = {}
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
        if self._session_factory is not None:
            return self._sql_create_run(compiled_plan, owner_scope, input_snapshot or {})
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

    def create_partial_run(
        self,
        *,
        source_run_id: uuid.UUID,
        compiled_plan: CompiledExecutionPlan,
        owner_scope: OwnerScope,
        closure: dict[str, list[str]],
    ) -> WorkflowRun:
        """Create an executable, immutable local-run slice.

        A slice never reads a Draft or a "latest" artifact.  Any non-executed
        upstream node is accepted only when its source NodeRun is completed,
        and the provider output ArtifactVersion ids are copied into the new
        run's input snapshot.  The derived graph contains *only* execute
        nodes, which makes accidental scheduling of skipped nodes impossible.
        """
        if self._session_factory is None:
            raise ConflictError("Partial runs require persistent runtime storage")
        execute = {str(value) for value in closure.get("execute", [])}
        reuse = {str(value) for value in closure.get("reuse", [])}
        if not execute or execute & reuse:
            raise ValidationError_("Partial run closure is invalid")
        with self._session_factory() as session:
            source = self._sql_required(session, WorkflowRunModel, source_run_id, "WorkflowRun")
            if source.owner_scope != owner_scope.scoped_id:
                raise ForbiddenError("Cannot create a partial run for another owner")
            source_nodes = {
                row.node_instance_id: row
                for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == source_run_id))
            }
            missing = sorted(node_id for node_id in reuse if node_id not in source_nodes or source_nodes[node_id].status != NodeRunStatus.COMPLETED)
            if missing:
                raise ConflictError(f"Partial run has unfixed upstream inputs: {', '.join(missing)}")
            frozen_outputs: dict[str, list[str]] = {}
            for node_id in sorted(reuse):
                node = source_nodes[node_id]
                artifact_ids = list(session.scalars(
                    select(ProviderOutputBindingModel.output_artifact_version_id)
                    .join(ProviderInvocationRecordModel, ProviderInvocationRecordModel.record_id == ProviderOutputBindingModel.record_id)
                    .join(ProviderInvocationAttemptModel, ProviderInvocationAttemptModel.provider_attempt_id == ProviderInvocationRecordModel.provider_attempt_id)
                    .join(NodeRunAttemptModel, NodeRunAttemptModel.attempt_id == ProviderInvocationAttemptModel.node_run_attempt_id)
                    .where(NodeRunAttemptModel.node_run_id == node.node_run_id)
                    .order_by(ProviderOutputBindingModel.output_index)
                ))
                if not artifact_ids:
                    # Map/Fold and non-provider nodes record their output ref
                    # on their fixed attempt input.  This is still a pinned
                    # artifact id, never a lookup by a mutable alias.
                    attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id).order_by(NodeRunAttemptModel.execution_epoch.desc()))
                    output = dict(attempt.fixed_input or {}).get("map_output") if attempt is not None else None
                    if isinstance(output, dict) and output.get("artifact_version_id"):
                        artifact_ids = [uuid.UUID(str(output["artifact_version_id"]))]
                if not artifact_ids:
                    raise ConflictError(f"Partial run upstream node {node_id} has no fixed output artifact")
                frozen_outputs[node_id] = [str(item) for item in artifact_ids]

        source_graph = compiled_plan.resolved_graph if isinstance(compiled_plan.resolved_graph, dict) else {}
        sliced_graph = {
            "nodes": [node for node in source_graph.get("nodes", []) if isinstance(node, dict) and str(node.get("id")) in execute],
            "edges": [edge for edge in source_graph.get("edges", []) if isinstance(edge, dict) and str(edge.get("source")) in execute and str(edge.get("target")) in execute],
        }
        slice_plan = compiled_plan.model_copy(update={"resolved_graph": sliced_graph})
        return self.create_run(
            compiled_plan=slice_plan,
            owner_scope=owner_scope,
            input_snapshot={
                "partial_run": {
                    "source_run_id": str(source_run_id),
                    "execute": sorted(execute),
                    "reuse": sorted(reuse),
                    "skip": sorted(str(value) for value in closure.get("skip", [])),
                    "fixed_upstream_outputs": frozen_outputs,
                },
            },
        )

    def start_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Transition run from QUEUED to RUNNING."""
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                row = self._sql_required(session, WorkflowRunModel, run_id, "WorkflowRun")
                if row.status != RunStatus.QUEUED:
                    raise ConflictError(f"运行 {run_id} 状态为 {row.status}，不能启动")
                row.status = RunStatus.RUNNING
                self._sql_materialize_workflow_gates(session, row)
                return self._run_schema(row)
        run = self._get_run(run_id)
        if run.status != RunStatus.QUEUED:
            raise ConflictError(f"运行 {run_id} 状态为 {run.status}，不能启动")

        run.status = RunStatus.RUNNING
        return run

    def cancel_run(self, run_id: uuid.UUID) -> WorkflowRun:
        """Cancel a run and all active attempts."""
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                run = self._sql_required(session, WorkflowRunModel, run_id, "WorkflowRun")
                if run.status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED):
                    raise ConflictError(f"运行 {run_id} 已结束")
                run.status = RunStatus.CANCELLING
                nodes = session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run_id)).all()
                for node in nodes:
                    if node.status in (NodeRunStatus.PENDING, NodeRunStatus.READY, NodeRunStatus.RUNNING, NodeRunStatus.WAITING_USER):
                        node.status = NodeRunStatus.CANCELLED
                        for attempt in session.scalars(select(NodeRunAttemptModel).where(
                            NodeRunAttemptModel.node_run_id == node.node_run_id,
                            NodeRunAttemptModel.status.in_([AttemptStatus.PENDING, AttemptStatus.LEASED, AttemptStatus.RUNNING, AttemptStatus.WAITING_EXTERNAL, AttemptStatus.UNKNOWN]),
                        )):
                            attempt.status = AttemptStatus.CANCELLED
                # Parent cancellation propagates to every fixed child run;
                # it never leaves an orphaned subworkflow consuming budget.
                for binding in session.scalars(select(SubworkflowModel).where(SubworkflowModel.run_id == run_id)):
                    if binding.child_run_id:
                        child = session.get(WorkflowRunModel, binding.child_run_id)
                        if child is not None and child.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                            child.status = RunStatus.CANCELLED
                    binding.status = "cancelled"
                session.add(OutboxEventModel(
                    event_id=uuid.uuid4(), aggregate_type="workflow_run", aggregate_id=run_id,
                    event_type="run.cancel_requested", purpose="runtime_cancel",
                    payload={"run_id": str(run_id)}, created_at=datetime.now(timezone.utc),
                ))
                run.status = RunStatus.CANCELLED
                session.flush()
                return self._run_schema(run)
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
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                node = self._sql_required(session, NodeRunModel, node_run_id, "NodeRun")
                count = session.scalar(select(func.count()).select_from(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node_run_id)) or 0
                row = NodeRunAttemptModel(
                    attempt_id=uuid.uuid4(), node_run_id=node_run_id,
                    attempt_number=count + 1, execution_epoch=count + 1,
                    fixed_input=fixed_input or {}, status=AttemptStatus.PENDING,
                )
                session.add(row)
                node.status = NodeRunStatus.RUNNING
                session.flush()
                return self._attempt_schema(row)
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
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                row = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                if row.status != AttemptStatus.PENDING:
                    raise ConflictError(f"Attempt {attempt_id} 状态为 {row.status}，不能设为运行中")
                row.status, row.lease_id = AttemptStatus.RUNNING, lease_id
                return self._attempt_schema(row)
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
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                if epoch is not None and attempt.execution_epoch != epoch:
                    raise ConflictError("执行纪元过期，结果被拒绝")
                if attempt.status in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}:
                    raise ConflictError("终态或过期 attempt 不能发布结果")
                attempt.status, attempt.completed_at = AttemptStatus.COMPLETED, datetime.now(timezone.utc)
                node = self._sql_required(session, NodeRunModel, attempt.node_run_id, "NodeRun")
                node.status = NodeRunStatus.COMPLETED
                run = self._sql_required(session, WorkflowRunModel, node.run_id, "WorkflowRun")
                self._sql_schedule_ready(session, run)
                session.flush()
                return self._attempt_schema(attempt)
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
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                if attempt.status in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED}:
                    raise ConflictError("终态或过期 attempt 不能失败发布")
                attempt.status, attempt.completed_at = AttemptStatus.FAILED, datetime.now(timezone.utc)
                node = self._sql_required(session, NodeRunModel, attempt.node_run_id, "NodeRun")
                node.status = NodeRunStatus.FAILED
                # A failed node may have one explicit Workflow-owned fallback.
                # Scheduling it here keeps the original failed attempt and
                # cost durable while allowing unrelated branches to continue.
                self._sql_schedule_ready(session, self._sql_required(session, WorkflowRunModel, node.run_id, "WorkflowRun"))
                session.flush()
                return self._attempt_schema(attempt)
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.FAILED
        attempt.completed_at = datetime.now(timezone.utc)

        node_run = self._get_node_run(attempt.node_run_id)
        node_run.status = NodeRunStatus.FAILED
        return attempt

    def supersede_attempt(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        """Mark attempt as superseded (new attempt invalidated this one)."""
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                attempt.status = AttemptStatus.SUPERSEDED
                session.flush()
                return self._attempt_schema(attempt)
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.SUPERSEDED
        return attempt

    def mark_unknown(self, attempt_id: uuid.UUID) -> NodeRunAttempt:
        """Mark attempt as unknown (provider response uncertain)."""
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                attempt.status = AttemptStatus.UNKNOWN
                session.flush()
                return self._attempt_schema(attempt)
        attempt = self._get_attempt(attempt_id)
        attempt.status = AttemptStatus.UNKNOWN
        return attempt

    def mark_provider_unknown(self, provider_attempt_id: uuid.UUID) -> None:
        """Persist an ambiguous provider submission for reconciliation only.

        Crucially this does not enqueue a retry: re-submitting an unknown
        external side effect would violate provider idempotency guarantees.
        """
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                provider = self._sql_required(session, ProviderInvocationAttemptModel, provider_attempt_id, "ProviderInvocationAttempt")
                attempt = self._sql_required(session, NodeRunAttemptModel, provider.node_run_attempt_id, "NodeRunAttempt")
                provider.status = AttemptStatus.UNKNOWN
                attempt.status = AttemptStatus.UNKNOWN
            return
        provider = self._get_provider_attempt(provider_attempt_id)
        provider.status = AttemptStatus.UNKNOWN
        self._get_attempt(provider.node_run_attempt_id).status = AttemptStatus.UNKNOWN

    def bind_provider_task(self, provider_attempt_id: uuid.UUID, provider_task_id: str) -> WorkflowTaskBinding:
        """Persist the provider's task id for callback/reconciliation dedupe."""
        if not provider_task_id:
            raise ConflictError("Provider task id is required")
        if self._session_factory is None:
            raise ConflictError("Provider task bindings require durable runtime storage")
        with self._session_factory.begin() as session:
            provider = self._sql_required(session, ProviderInvocationAttemptModel, provider_attempt_id, "ProviderInvocationAttempt")
            prior = session.scalar(select(WorkflowTaskBindingModel).where(
                WorkflowTaskBindingModel.provider_attempt_id == provider_attempt_id,
            ))
            if prior is not None:
                if prior.provider_task_id != provider_task_id:
                    raise ConflictError("ProviderInvocationAttempt already has a different task binding")
                return self._task_binding_schema(prior)
            by_task = session.scalar(select(WorkflowTaskBindingModel).where(
                WorkflowTaskBindingModel.provider_task_id == provider_task_id,
            ))
            if by_task is not None:
                raise ConflictError("Provider task id is already bound to another invocation")
            row = WorkflowTaskBindingModel(
                binding_id=uuid.uuid4(), node_run_attempt_id=provider.node_run_attempt_id,
                provider_attempt_id=provider_attempt_id, provider_task_id=provider_task_id,
                task_status="submitted",
            )
            session.add(row)
            session.flush()
            return self._task_binding_schema(row)

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
        if self._session_factory is not None:
            return self._sql_dispatch_provider(attempt_id, provider_id, model_id, idempotency_key, request_body_hash)
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
        if self._session_factory is not None:
            return self._sql_record_provider_result(
                provider_attempt_id, model_version, response_fingerprint, usage or {},
                actual_cost, output_artifact_version_ids or [], current_epoch,
            )
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
                self._output_bindings[binding.binding_id] = binding

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
        """Materialise a workflow-owned gate from a fixed running revision.

        This is an internal runtime operation.  It intentionally derives
        ownership from the run rather than accepting an arbitrary revision or
        owner-layer from an API caller.
        """
        if task_kind != "human_gate":
            raise ConflictError("Runtime may only materialise workflow-owned Human Gates")
        if policy_strength not in {"advisory", "domain_required", "policy_required"}:
            raise ConflictError("Invalid Human Gate policy strength")
        if timeout_minutes < 1:
            raise ConflictError("Human Gate timeout must be declared and positive")
        if self._session_factory is not None:
            assert self._session_factory is not None
            with self._session_factory.begin() as session:
                run = self._sql_required(session, WorkflowRunModel, run_id, "WorkflowRun")
                node = self._sql_required(session, NodeRunModel, node_run_id, "NodeRun")
                attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
                if node.run_id != run_id or attempt.node_run_id != node_run_id:
                    raise ConflictError("Human Gate must bind the run's current node attempt")
                sql_task = HumanTaskModel(
                    task_id=uuid.uuid4(), task_kind=task_kind, owner_layer="workflow",
                    owner_revision_id=run.workflow_revision_id, run_id=run_id, node_run_id=node_run_id,
                    attempt_id=attempt_id, input_snapshot_refs=self._json_refs(input_snapshot_refs or []),
                    policy_strength=policy_strength,
                    timeout_policy={"duration_minutes": timeout_minutes, "on_timeout": "fail"},
                    status=HumanTaskStatus.PENDING, created_at=datetime.now(timezone.utc),
                )
                session.add(sql_task)
                node.status = NodeRunStatus.WAITING_USER
                run.status = RunStatus.WAITING_USER
                session.flush()
                return self._human_task_schema(sql_task)
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

    # Idempotent terminal transitions for human tasks.
    # Each of accept / reject / timeout
    #   * on first call: drives the transition, emits one outbox event of the
    #     matching type, and clears the run/owner_state if needed
    #   * on subsequent calls: returns the same persisted state without
    #     mutating it or emitting a second event.
    # All terminal states share this idempotency contract so retrying from
    # the UI does not double-publish.
    _TERMINAL_HUMAN_STATES: frozenset[HumanTaskStatus] = frozenset({
        HumanTaskStatus.ACCEPTED,
        HumanTaskStatus.REJECTED,
        HumanTaskStatus.EXPIRED,
    })

    def _terminate_human_task(
        self,
        *,
        task_id: uuid.UUID,
        terminal_status: HumanTaskStatus,
        event_type: str,
        outcome: str,
        reason: str | None = None,
        actor_id: uuid.UUID | None = None,
        actor_scope: str | None = None,
        task_version: int | None = None,
        idempotency_token: str | None = None,
        typed_payload: dict[str, Any] | None = None,
        policy_evidence_refs: list[str] | None = None,
        internal: bool = True,
    ) -> tuple[HumanTaskRecord, uuid.UUID | None, bool]:
        """Shared idempotent terminal-transition for human tasks.

        Returns ``(task, outbox_event_id, changed)``.  When ``changed`` is
        ``False``, the task was already in a terminal state and was returned
        unchanged with no new outbox event.
        """
        if self._session_factory is not None:
            assert self._session_factory is not None
            with self._session_factory.begin() as session:
                task = session.get(HumanTaskModel, task_id, with_for_update=True)
                if task is None:
                    raise NotFoundError("HumanTask", str(task_id))
                effective_version = task.task_version if task_version is None else task_version
                if effective_version != task.task_version:
                    raise ConflictError("Human Gate task version 已过期")
                if not internal:
                    if actor_id is None or not actor_scope or not idempotency_token:
                        raise ValidationError_("Human Gate 决策需要已认证 actor、task_version 与 idempotency_token")
                    self._authorize_human_gate(session, task, actor_scope)
                # Direct service calls are test/internal orchestration only.  They
                # still leave an explicit audit identity rather than silently
                # creating an anonymous decision.
                resolved_actor_id = actor_id or uuid.UUID(int=0)
                resolved_actor_scope = actor_scope or "system:runtime"
                resolved_token = idempotency_token or f"internal:{uuid.uuid4()}"
                prior = session.scalar(select(HumanTaskDecisionModel).where(
                    HumanTaskDecisionModel.task_id == task_id,
                    HumanTaskDecisionModel.idempotency_token == resolved_token,
                ))
                if prior is not None:
                    return self._human_task_schema(task), None, False
                if task.status in self._TERMINAL_HUMAN_STATES:
                    if not internal:
                        raise ConflictError("Human Gate 已有终态裁决；请使用原 idempotency_token 重试")
                    return self._human_task_schema(task), None, False
                decision = HumanTaskDecisionModel(
                    decision_id=uuid.uuid4(), task_id=task.task_id,
                    task_version=task.task_version, action=outcome,
                    actor_id=resolved_actor_id, actor_scope=resolved_actor_scope,
                    typed_payload=typed_payload or {}, notes=reason or "",
                    policy_evidence_refs=policy_evidence_refs or [],
                    idempotency_token=resolved_token, created_at=datetime.now(timezone.utc),
                )
                session.add(decision)
                task.status = terminal_status
                # Surface rejection / timeout context on the row for replay.
                if reason:
                    existing = dict(task.timeout_policy or {})
                    existing["last_reason"] = reason
                    task.timeout_policy = existing
                # If accept → drive node + run back to running/completed
                if terminal_status == HumanTaskStatus.ACCEPTED:
                    node = self._sql_required(session, NodeRunModel, task.node_run_id, "NodeRun")
                    attempt = self._sql_required(session, NodeRunAttemptModel, task.attempt_id, "NodeRunAttempt")
                    fixed_input = dict(attempt.fixed_input or {})
                    fixed_input["human_gate_decision"] = {
                        "task_id": str(task.task_id), "action": outcome,
                        "payload": typed_payload or {}, "task_version": task.task_version,
                    }
                    attempt.fixed_input = fixed_input
                    attempt.status = AttemptStatus.RUNNING
                    node.status = NodeRunStatus.RUNNING
                    run = self._sql_required(session, WorkflowRunModel, task.run_id, "WorkflowRun")
                    if run.status == RunStatus.WAITING_USER:
                        run.status = RunStatus.RUNNING
                elif outcome == "cancel":
                    node = self._sql_required(session, NodeRunModel, task.node_run_id, "NodeRun")
                    node.status = NodeRunStatus.CANCELLED
                    run = self._sql_required(session, WorkflowRunModel, task.run_id, "WorkflowRun")
                    run.status = RunStatus.CANCELLED
                elif terminal_status in (HumanTaskStatus.REJECTED, HumanTaskStatus.EXPIRED):
                    node = self._sql_required(session, NodeRunModel, task.node_run_id, "NodeRun")
                    if node.status != NodeRunStatus.FAILED:
                        node.status = NodeRunStatus.FAILED
                    run = self._sql_required(session, WorkflowRunModel, task.run_id, "WorkflowRun")
                    # Run-level failure is durable; reversible flows use cancel.
                    run.status = RunStatus.FAILED
                event = OutboxEventModel(
                    event_id=uuid.uuid4(),
                    aggregate_type="human_task",
                    aggregate_id=task.task_id,
                    event_type=event_type,
                    payload={
                        "outcome": outcome,
                        "run_id": str(task.run_id),
                        "node_run_id": str(task.node_run_id),
                        "decision_reason": reason or "",
                        "decision_id": str(decision.decision_id),
                        "task_version": task.task_version,
                    },
                    purpose="human_gate_terminal",
                    created_at=datetime.now(timezone.utc),
                )
                session.add(event)
                session.flush()
                return self._human_task_schema(task), event.event_id, True
        task = self._human_tasks.get(task_id)
        if not task:
            raise NotFoundError("HumanTask", str(task_id))
        if task.status in self._TERMINAL_HUMAN_STATES:
            return task, None, False
        task.status = terminal_status
        node_run = self._get_node_run(task.node_run_id)
        run = self._get_run(task.run_id)
        if terminal_status == HumanTaskStatus.ACCEPTED:
            node_run.status = NodeRunStatus.RUNNING
            if run.status == RunStatus.WAITING_USER:
                run.status = RunStatus.RUNNING
        elif outcome == "cancel":
            node_run.status = NodeRunStatus.CANCELLED
            run.status = RunStatus.CANCELLED
        elif terminal_status in (HumanTaskStatus.REJECTED, HumanTaskStatus.EXPIRED):
            node_run.status = NodeRunStatus.FAILED
            run.status = RunStatus.FAILED
        return task, uuid.uuid4(), True

    def resolve_human_task(
        self,
        task_id: uuid.UUID,
        *,
        decision: str = "accept",
        idempotency_key: str = "",
        payload: dict[str, Any] | None = None,
        actor_id: uuid.UUID | None = None,
        actor_scope: str | None = None,
        task_version: int | None = None,
        policy_evidence_refs: list[str] | None = None,
        internal: bool = True,
        strict: bool = False,
    ) -> HumanTaskRecord:
        """Resolve a human task with idempotency.

        ``decision='accept'`` → ACCEPTED.  ``decision='reject'`` → REJECTED.
        Other values fall back to SUBMITTED.

        ``strict=True`` raises :class:`ConflictError` on a second call,
        used by tests and Demo UI replays.
        """
        if decision == "reject":
            terminal = HumanTaskStatus.REJECTED
            event_type = "human_task.rejected"
            outcome = "reject"
        else:
            terminal = HumanTaskStatus.ACCEPTED
            event_type = "human_task.accepted"
            outcome = "accept"
        task, _event_id, changed = self._terminate_human_task(
            task_id=task_id,
            terminal_status=terminal,
            event_type=event_type,
            outcome=outcome,
            reason=(payload or {}).get("decision_reason") if payload else None,
            actor_id=actor_id,
            actor_scope=actor_scope,
            task_version=task_version,
            idempotency_token=idempotency_key or None,
            typed_payload=payload or {},
            policy_evidence_refs=policy_evidence_refs,
            internal=internal,
        )
        if strict and not changed:
            raise ConflictError(
                message=f"HumanTask {task_id} 已经处于终态 {task.status.value}"
            )
        return task

    def reject_human_task(
        self,
        task_id: uuid.UUID,
        *,
        reason: str = "",
        actor_id: uuid.UUID | None = None,
        actor_scope: str | None = None,
        task_version: int | None = None,
        idempotency_key: str = "",
        policy_evidence_refs: list[str] | None = None,
        internal: bool = True,
        strict: bool = False,
    ) -> HumanTaskRecord:
        """Reject a human task. Idempotent on retry; ``strict=True`` raises on retry."""
        task, _event_id, changed = self._terminate_human_task(
            task_id=task_id,
            terminal_status=HumanTaskStatus.REJECTED,
            event_type="human_task.rejected",
            outcome="reject",
            reason=reason or None,
            actor_id=actor_id,
            actor_scope=actor_scope,
            task_version=task_version,
            idempotency_token=idempotency_key or None,
            typed_payload={},
            policy_evidence_refs=policy_evidence_refs,
            internal=internal,
        )
        if strict and not changed:
            raise ConflictError(
                message=f"HumanTask {task_id} 已经处于终态 {task.status.value}"
            )
        return task

    def timeout_human_task(
        self,
        task_id: uuid.UUID,
        *,
        reason: str = "",
        actor_id: uuid.UUID | None = None,
        actor_scope: str | None = None,
        task_version: int | None = None,
        idempotency_key: str = "",
        internal: bool = True,
        strict: bool = False,
    ) -> HumanTaskRecord:
        """Timeout a human task. Idempotent on retry; ``strict=True`` raises on retry."""
        # Timeout behaviour is frozen with the task.  "default" resumes the
        # same fixed attempt with its declared payload; the other policies
        # terminate safely and leave an auditable event for the worker/ops path.
        timeout_policy = self._timeout_policy(task_id)
        action = str(timeout_policy.get("on_timeout", "fail"))
        if action not in {"fail", "cancel", "default", "escalate"}:
            raise ConflictError("Human Gate timeout policy 非法")
        if action == "default":
            terminal, event_type, outcome = HumanTaskStatus.ACCEPTED, "human_task.defaulted", "default"
            payload = timeout_policy.get("default_payload", {})
            if not isinstance(payload, dict):
                raise ConflictError("Human Gate default timeout payload 必须是对象")
        elif action == "cancel":
            terminal, event_type, outcome, payload = HumanTaskStatus.EXPIRED, "human_task.cancelled", "cancel", {}
        elif action == "escalate":
            terminal, event_type, outcome, payload = HumanTaskStatus.EXPIRED, "human_task.escalated", "escalate", {}
        else:
            terminal, event_type, outcome, payload = HumanTaskStatus.EXPIRED, "human_task.expired", "fail", {}
        task, _event_id, changed = self._terminate_human_task(
            task_id=task_id,
            terminal_status=terminal,
            event_type=event_type,
            outcome=outcome,
            reason=reason or None,
            actor_id=actor_id,
            actor_scope=actor_scope,
            task_version=task_version,
            idempotency_token=idempotency_key or None,
            typed_payload=payload,
            internal=internal,
        )
        if strict and not changed:
            raise ConflictError(
                message=f"HumanTask {task_id} 已经处于终态 {task.status.value}"
            )
        return task

    def expire_due_human_tasks(self, *, now: datetime | None = None) -> int:
        """Worker scanner for durable task deadlines; notifications are irrelevant."""
        if self._session_factory is None:
            return 0
        now = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            tasks = list(session.scalars(select(HumanTaskModel).where(HumanTaskModel.status.in_([HumanTaskStatus.PENDING, HumanTaskStatus.WAITING]))))
        due: list[uuid.UUID] = []
        for task in tasks:
            minutes = int((task.timeout_policy or {}).get("duration_minutes", 0))
            created = task.created_at.replace(tzinfo=timezone.utc) if task.created_at and task.created_at.tzinfo is None else task.created_at
            if minutes > 0 and created and created.timestamp() + minutes * 60 <= now.timestamp():
                due.append(task.task_id)
        for task_id in due:
            self.timeout_human_task(task_id, reason="deadline scanner")
        return len(due)

    # ------------------------------------------------------------------
    # Outbox & Recovery
    # ------------------------------------------------------------------

    def get_pending_outbox(self) -> list[OutboxEvent]:
        """Get un-published outbox events."""
        if self._session_factory is not None:
            with self._session_factory() as session:
                rows = session.scalars(select(OutboxEventModel).where(OutboxEventModel.published_at.is_(None)).order_by(OutboxEventModel.created_at)).all()
                return [self._outbox_schema(row) for row in rows]
        return [e for e in self._outbox if e.published_at is None]

    def mark_outbox_published(self, event_id: uuid.UUID):
        """Mark an outbox event as published."""
        if self._session_factory is not None:
            with self._session_factory.begin() as session:
                row = self._sql_required(session, OutboxEventModel, event_id, "OutboxEvent")
                row.published_at = datetime.now(timezone.utc)
            return
        for event in self._outbox:
            if event.event_id == event_id:
                event.published_at = datetime.now(timezone.utc)
                break

    def recover(self) -> dict[str, Any]:
        """Recover state after restart — return summary of active runs."""
        if self._session_factory is not None:
            with self._session_factory() as session:
                active = session.scalar(select(func.count()).select_from(WorkflowRunModel).where(WorkflowRunModel.status.in_([RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_USER, RunStatus.CANCELLING]))) or 0  # type: ignore[attr-defined]
                pending = session.scalar(select(func.count()).select_from(OutboxEventModel).where(OutboxEventModel.published_at.is_(None))) or 0  # type: ignore[arg-type]
                waiting = session.scalar(select(func.count()).select_from(NodeRunAttemptModel).where(NodeRunAttemptModel.status == AttemptStatus.WAITING_EXTERNAL)) or 0  # type: ignore[arg-type]
                unknown = session.scalar(select(func.count()).select_from(NodeRunAttemptModel).where(NodeRunAttemptModel.status == AttemptStatus.UNKNOWN)) or 0  # type: ignore[arg-type]
                return {"active_runs": active, "pending_outbox": pending, "waiting_external": waiting, "unknown_attempts": unknown}
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
    # SQL persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sql_required(session: Any, model: Any, entity_id: uuid.UUID, name: str) -> Any:
        row = session.get(model, entity_id)
        if row is None:
            raise NotFoundError(name, str(entity_id))
        return row

    @staticmethod
    def _authorize_human_gate(session: Any, task: HumanTaskModel, actor_scope: str) -> None:
        """Enforce the V1 owner/assignee boundary from server-authenticated scope."""
        run = session.get(WorkflowRunModel, task.run_id)
        if run is None:
            raise NotFoundError("WorkflowRun", str(task.run_id))
        if task.task_kind != "human_gate" or task.owner_layer != "workflow":
            raise ForbiddenError("该接口只允许处理固定 Workflow Human Gate")
        if task.assignee_scope and task.assignee_scope != actor_scope:
            raise ForbiddenError("Human Gate 已分配给其他处理者")
        if run.owner_scope != actor_scope:
            raise ForbiddenError("只有当前项目 owner 可以处理 Human Gate")
        if task.policy_strength == "policy_required":
            # A project owner must never self-assert policy evidence.  A future
            # platform-review service will use a separate internal endpoint.
            raise PolicyBlockedError("policy_required Gate 需要平台审核主体")

    def _timeout_policy(self, task_id: uuid.UUID) -> dict[str, Any]:
        if self._session_factory is not None:
            with self._session_factory() as session:
                task = self._sql_required(session, HumanTaskModel, task_id, "HumanTask")
                return dict(task.timeout_policy or {})
        task = self._human_tasks.get(task_id)
        if task is None:
            raise NotFoundError("HumanTask", str(task_id))
        return dict(task.timeout_policy or {})

    def _sql_materialize_workflow_gates(self, session: Any, run: WorkflowRunModel) -> None:
        """Materialise only fixed-revision Human Gate nodes for this run.

        This is deliberately inside ``start_run``'s transaction.  No HTTP
        caller, Agent, Recipe or Workbench can manufacture a workflow Gate.
        """
        revision = self._sql_required(session, WorkflowRevisionModel, run.workflow_revision_id, "WorkflowRevision")
        graph = revision.graph or {}
        nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
        for spec in nodes:
            if not isinstance(spec, dict) or spec.get("type") != "human_gate":
                continue
            node_id = str(spec.get("id", ""))
            node = session.scalar(select(NodeRunModel).where(
                NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == node_id,
            ))
            if node is None:
                raise ConflictError(f"Compiled Human Gate node {node_id} is missing its NodeRun")
            # Only roots are eligible at run start. Downstream gates are
            # materialised by _sql_schedule_ready after their predecessors.
            if node.status == NodeRunStatus.READY:
                self._sql_materialize_workflow_gate(session, run, node, spec)
        if run.status != RunStatus.QUEUED:
            self._sql_aggregate_run(session, run)

    def _sql_materialize_workflow_gate(self, session: Any, run: WorkflowRunModel, node: NodeRunModel, spec: dict[str, Any]) -> None:
        existing = session.scalar(select(HumanTaskModel).where(HumanTaskModel.node_run_id == node.node_run_id))
        if existing is not None:
            return
        gate = self._node_config(spec)
        strength = str(gate.get("policy_strength", "domain_required"))
        timeout_minutes = int(gate.get("timeout_minutes", 60))
        if strength not in {"advisory", "domain_required", "policy_required"} or timeout_minutes < 1:
            raise ConflictError("Compiled Human Gate has invalid immutable policy")
        attempt = NodeRunAttemptModel(
            attempt_id=uuid.uuid4(), node_run_id=node.node_run_id,
            attempt_number=1, execution_epoch=1, fixed_input=dict(run.input_snapshot or {}),
            status=AttemptStatus.PENDING,
        )
        session.add(attempt)
        session.flush()
        session.add(HumanTaskModel(
            task_id=uuid.uuid4(), task_kind="human_gate", owner_layer="workflow",
            owner_revision_id=run.workflow_revision_id, run_id=run.run_id,
            node_run_id=node.node_run_id, attempt_id=attempt.attempt_id,
            input_snapshot_refs=[], policy_strength=strength, schema_ref="",
            timeout_policy={"duration_minutes": timeout_minutes, "on_timeout": gate.get("on_timeout", "fail")},
            status=HumanTaskStatus.PENDING, task_version=1, created_at=datetime.now(timezone.utc),
        ))
        node.status = NodeRunStatus.WAITING_USER

    def _sql_create_run(self, plan: CompiledExecutionPlan, owner: OwnerScope, inputs: dict[str, Any]) -> WorkflowRun:
        assert self._session_factory is not None
        with self._session_factory.begin() as session:
            row = WorkflowRunModel(
                run_id=uuid.uuid4(), workflow_revision_id=plan.workflow_revision_id,
                compiled_plan_id=plan.plan_id, owner_scope=owner.scoped_id,
                input_snapshot=inputs, status=RunStatus.QUEUED,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            # Models deliberately avoid ORM relationships; flush the parent before
            # inserting child runs so PostgreSQL sees the FK target.
            session.flush()
            nodes = [node for node in plan.resolved_graph.get("nodes", []) if isinstance(node, dict)]
            edges = [edge for edge in plan.resolved_graph.get("edges", []) if isinstance(edge, dict)]
            incoming = {str(edge.get("target", "")) for edge in edges}
            for node_data in nodes:
                data = node_data if isinstance(node_data, dict) else {}
                session.add(NodeRunModel(
                    node_run_id=uuid.uuid4(), run_id=row.run_id,
                    node_instance_id=str(data.get("id", "unknown")),
                    node_type_id=str(data.get("type", "unknown")),
                    # Only graph roots are initially eligible.  Downstream
                    # nodes become READY after their fixed dependencies settle.
                    status=NodeRunStatus.READY if str(data.get("id", "")) not in incoming else NodeRunStatus.PENDING,
                ))
            session.flush()
            self._sql_schedule_ready(session, row, graph={"nodes": nodes, "edges": edges})
            return self._run_schema(row)

    def _sql_schedule_ready(self, session: Any, run: WorkflowRunModel, *, graph: dict[str, Any] | None = None) -> None:
        """Materialise ready attempts from the immutable graph, never latest Draft.

        This is deliberately a small deterministic scheduler: it gates every
        non-root node on terminal upstream NodeRuns, records skipped condition
        branches, and creates exactly one initial attempt for a ready node.
        More specialised executors (map/fold/subworkflow) consume that same
        attempt rather than allowing a public side API to manufacture state.
        """
        if graph is None:
            revision = self._sql_required(session, WorkflowRevisionModel, run.workflow_revision_id, "WorkflowRevision")
            graph = revision.graph if isinstance(revision.graph, dict) else {}
        specs = {str(node.get("id", "")): node for node in graph.get("nodes", []) if isinstance(node, dict)}
        edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
        rows = list(session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id)))
        by_id = {row.node_instance_id: row for row in rows}

        # A completed Condition selects its configured default branch unless a
        # worker persisted an explicit selected_branch in its fixed input.
        for edge in edges:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            source_spec, source_row, target_row = specs.get(source, {}), by_id.get(source), by_id.get(target)
            if not source_row or not target_row or source_spec.get("type") != "condition" or source_row.status != NodeRunStatus.COMPLETED:
                continue
            config = self._node_config(source_spec)
            selected = str(config.get("selected_branch") or config.get("default_branch") or "")
            branch = str(edge.get("sourceHandle") or edge.get("source_handle") or target)
            if selected and branch != selected and target_row.status in {NodeRunStatus.PENDING, NodeRunStatus.READY}:
                target_row.status = NodeRunStatus.SKIPPED

        for node_id, row in by_id.items():
            if row.status not in {NodeRunStatus.PENDING, NodeRunStatus.READY}:
                continue
            # A Media Recipe is a frozen second-level DAG.  Its child nodes do
            # not appear in the outer WorkflowRevision graph, therefore the
            # generic scheduler must never mistake them for root workflow
            # nodes. RecipeRuntimeService owns their dependency readiness.
            if row.node_type_id.startswith("recipe."):
                continue
            spec = specs.get(node_id, {})
            predecessors = [str(edge.get("source", "")) for edge in edges if str(edge.get("target", "")) == node_id]
            predecessor_rows = [by_id[source] for source in predecessors if source in by_id]
            cfg = self._node_config(spec)
            failed_predecessors = [item for item in predecessor_rows if item.status == NodeRunStatus.FAILED]
            fallback_sources = [
                item for item in failed_predecessors
                if self._node_config(specs.get(item.node_instance_id, {})).get("failure_policy") == "configured_fallback"
                and str(self._node_config(specs.get(item.node_instance_id, {})).get("fallback_node_id", "")) == node_id
            ]
            if spec.get("type") == "join" and cfg.get("strategy") == "any":
                ready = any(item.status == NodeRunStatus.COMPLETED for item in predecessor_rows)
            else:
                # A fallback target is ready only after all its sources are
                # terminal and at least one declared source failed.  Every
                # other failed dependency closes this branch deterministically.
                ready = all(item.status in {NodeRunStatus.COMPLETED, NodeRunStatus.SKIPPED, NodeRunStatus.FAILED} for item in predecessor_rows)
                if failed_predecessors:
                    ready = ready and len(fallback_sources) == len(failed_predecessors)
            if not ready:
                # A failure without a declared edge-local fallback closes only
                # its dependent branch.  It never cancels unrelated nodes.
                if failed_predecessors and all(item.status in {NodeRunStatus.COMPLETED, NodeRunStatus.SKIPPED, NodeRunStatus.FAILED} for item in predecessor_rows):
                    row.status = NodeRunStatus.SKIPPED
                continue
            if predecessor_rows and all(item.status == NodeRunStatus.SKIPPED for item in predecessor_rows):
                row.status = NodeRunStatus.SKIPPED
                continue
            row.status = NodeRunStatus.READY
            if spec.get("type") == "human_gate":
                self._sql_materialize_workflow_gate(session, run, row, spec)
                continue
            self._sql_materialize_control_state(session, run, row, spec)
            if spec.get("type") in {"map", "ordered_map", "fold", "subworkflow_call"}:
                # These nodes are executed by their durable expanded units,
                # never by a synthetic parent attempt.
                row.status = NodeRunStatus.RUNNING
                continue
            existing = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == row.node_run_id))
            if existing is None:
                committed_refs: list[dict[str, Any]] = []
                upstream_artifact_refs: list[dict[str, Any]] = []
                for predecessor in predecessor_rows:
                    predecessor_attempt = session.scalar(select(NodeRunAttemptModel).where(
                        NodeRunAttemptModel.node_run_id == predecessor.node_run_id,
                        NodeRunAttemptModel.status == AttemptStatus.COMPLETED,
                    ).order_by(NodeRunAttemptModel.execution_epoch.desc()))
                    if predecessor_attempt is not None:
                        committed_refs.extend(list((predecessor_attempt.fixed_input or {}).get("committed_resource_refs", [])))
                        artifact_ids = list(session.scalars(
                            select(ProviderOutputBindingModel.output_artifact_version_id)
                            .join(ProviderInvocationRecordModel, ProviderInvocationRecordModel.record_id == ProviderOutputBindingModel.record_id)
                            .join(ProviderInvocationAttemptModel, ProviderInvocationAttemptModel.provider_attempt_id == ProviderInvocationRecordModel.provider_attempt_id)
                            .where(ProviderInvocationAttemptModel.node_run_attempt_id == predecessor_attempt.attempt_id)
                            .order_by(ProviderOutputBindingModel.output_index)
                        ))
                        if not artifact_ids:
                            map_ref = dict(predecessor_attempt.fixed_input or {}).get("map_output")
                            if isinstance(map_ref, dict) and map_ref.get("artifact_version_id"):
                                artifact_ids = [uuid.UUID(str(map_ref["artifact_version_id"]))]
                        if not artifact_ids:
                            recipe_outputs = dict(predecessor_attempt.fixed_input or {}).get("recipe_output_artifact_version_ids", [])
                            if isinstance(recipe_outputs, list):
                                artifact_ids = [uuid.UUID(str(value)) for value in recipe_outputs]
                        if artifact_ids:
                            upstream_artifact_refs.append({
                                "source_node_id": predecessor.node_instance_id,
                                "artifact_version_ids": [str(value) for value in artifact_ids],
                            })
                node_config = self._node_config(spec)
                fixed_input: dict[str, Any] = {
                    **dict(run.input_snapshot or {}),
                    "committed_resource_refs": committed_refs,
                    "upstream_artifact_refs": upstream_artifact_refs,
                }
                if node_config.get("agent_revision_id"):
                    fixed_input["agent_revision_id"] = str(node_config["agent_revision_id"])
                if node_config.get("media_recipe_revision_id"):
                    fixed_input["media_recipe_revision_id"] = str(node_config["media_recipe_revision_id"])
                if fallback_sources:
                    fixed_input["fallback_for_node_ids"] = [item.node_instance_id for item in fallback_sources]
                session.add(NodeRunAttemptModel(
                    attempt_id=uuid.uuid4(), node_run_id=row.node_run_id, attempt_number=1,
                    execution_epoch=1, fixed_input=fixed_input, status=AttemptStatus.PENDING,
                ))
        if run.status != RunStatus.QUEUED:
            self._sql_aggregate_run(session, run)

    @staticmethod
    def _node_config(spec: dict[str, Any]) -> dict[str, Any]:
        direct = spec.get("config")
        nested = spec.get("data", {}).get("config") if isinstance(spec.get("data"), dict) else None
        return direct if isinstance(direct, dict) else nested if isinstance(nested, dict) else {}

    def _sql_materialize_control_state(self, session: Any, run: WorkflowRunModel, node: NodeRunModel, spec: dict[str, Any]) -> None:
        """Create bounded control records from the plan, never from an API body."""
        kind, cfg = str(spec.get("type", "")), self._node_config(spec)
        if kind == "condition":
            if session.scalar(select(ConditionModel).where(ConditionModel.run_id == run.run_id, ConditionModel.node_instance_id == node.node_instance_id)) is None:
                session.add(ConditionModel(condition_id=uuid.uuid4(), run_id=run.run_id, node_instance_id=node.node_instance_id,
                    operator=str(cfg.get("operator", "exists")), threshold=cfg.get("threshold"), value_path=cfg.get("value_path"), expression=cfg.get("expression"), config=cfg))
        elif kind == "join":
            if session.scalar(select(JoinModel).where(JoinModel.run_id == run.run_id, JoinModel.node_instance_id == node.node_instance_id)) is None:
                declared = str(cfg.get("strategy", "all"))
                persisted_strategy = {"all": "and", "any": "or", "merge": "and"}.get(declared, declared)
                session.add(JoinModel(join_id=uuid.uuid4(), run_id=run.run_id, node_instance_id=node.node_instance_id,
                    strategy=persisted_strategy, source_node_ids=list(cfg.get("source_node_ids", [])), config=cfg))
        elif kind in {"map", "ordered_map", "fold"}:
            existing = session.scalar(select(ForEachRunModel).where(ForEachRunModel.run_id == run.run_id, ForEachRunModel.node_instance_id == node.node_instance_id))
            if existing is not None:
                return
            values = cfg.get("items", (run.input_snapshot or {}).get(node.node_instance_id, []))
            if not isinstance(values, list) or len(values) > int(cfg.get("max_items", 0)):
                raise ConflictError("Compiled Map input violates fixed max_items")
            mode = "sequential" if kind in {"ordered_map", "fold"} else "parallel"
            frozen_cfg = {**cfg, "fold": kind == "fold"}
            raw_agent_revision = cfg.get("agent_revision_id")
            map_item_node_type = "map_item"
            if raw_agent_revision is not None:
                try:
                    map_item_node_type = f"agent.invoke.{uuid.UUID(str(raw_agent_revision))}"
                except (TypeError, ValueError) as exc:
                    raise ValidationError_("Map Agent executor requires a valid fixed agent_revision_id") from exc
            flow = ForEachRunModel(for_each_id=uuid.uuid4(), run_id=run.run_id, node_instance_id=node.node_instance_id,
                mode=mode, item_count=len(values), status="running", config=frozen_cfg)
            session.add(flow)
            pending_attempts: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any]]] = []
            for index, value in enumerate(values):
                item_id = uuid.uuid4()
                fixed_value = value if isinstance(value, dict) else {"value": value}
                session.add(MapItemRunModel(map_item_id=item_id, run_id=run.run_id, node_instance_id=node.node_instance_id,
                    item_key=str(index), item_index=index, item_value=fixed_value, status="pending"))
                item_node = NodeRunModel(node_run_id=uuid.uuid4(), run_id=run.run_id,
                    node_instance_id=f"{node.node_instance_id}[{index}]", node_type_id=map_item_node_type, status=NodeRunStatus.READY)
                session.add(item_node)
                pending_attempts.append((item_node.node_run_id, item_id, fixed_value))
            # ORM relationships are intentionally absent in these models.
            # Flush the expanded NodeRuns before their FK-bound attempts so
            # PostgreSQL never receives child rows first in a bulk insert.
            session.flush()
            for index, (item_node_id, item_id, fixed_value) in enumerate(pending_attempts):
                session.add(NodeRunAttemptModel(attempt_id=uuid.uuid4(), node_run_id=item_node_id,
                    attempt_number=1, execution_epoch=1,
                    fixed_input={
                        "map_parent_node_id": node.node_instance_id, "map_item_id": str(item_id),
                        "item_index": index, "item": fixed_value,
                        **({"agent_revision_id": str(raw_agent_revision)} if raw_agent_revision is not None else {}),
                    },
                    status=AttemptStatus.PENDING))
        elif kind == "subworkflow_call":
            if session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == run.run_id, SubworkflowModel.node_instance_id == node.node_instance_id)) is not None:
                return
            child_id = uuid.UUID(str(cfg["workflow_revision_id"]))
            child_revision = self._sql_required(session, WorkflowRevisionModel, child_id, "WorkflowRevision")
            child_workflow = self._sql_required(session, WorkflowModel, child_revision.workflow_id, "Workflow")
            if child_workflow.owner_scope != run.owner_scope:
                raise ForbiddenError("Subworkflow cannot expand the parent owner scope")
            # Walk actual parent bindings rather than trusting a Draft config
            # ``depth`` field.  This closes indirect A -> B -> A recursion.
            ancestry_revisions = {run.workflow_revision_id}
            current_run_id = run.run_id
            actual_depth = 0
            while True:
                ancestor = session.scalar(select(SubworkflowModel).where(SubworkflowModel.child_run_id == current_run_id))
                if ancestor is None:
                    break
                current_run_id = ancestor.run_id
                parent_run = self._sql_required(session, WorkflowRunModel, current_run_id, "WorkflowRun")
                ancestry_revisions.add(parent_run.workflow_revision_id)
                actual_depth += 1
            if child_id in ancestry_revisions:
                raise ConflictError("Recursive SubworkflowCall is forbidden")
            max_depth = int(cfg.get("max_depth", 0))
            if max_depth < 1 or actual_depth + 1 > max_depth:
                raise ConflictError("Subworkflow exceeds fixed max_depth")
            child_plan = session.scalar(select(CompiledExecutionPlanModel).where(
                CompiledExecutionPlanModel.workflow_revision_id == child_id,
                CompiledExecutionPlanModel.status == "succeeded",
            ).order_by(CompiledExecutionPlanModel.created_at.desc()).limit(1))
            if child_plan is None:
                raise ConflictError("Subworkflow requires a successful immutable child plan")
            self._sql_validate_subworkflow_mappings(cfg)
            self._sql_validate_subworkflow_inputs(session, run, cfg)
            self._sql_validate_subworkflow_capabilities(session, run, child_plan)
            child_graph = child_plan.plan_json.get("resolved_graph", {}) if isinstance(child_plan.plan_json, dict) else {}
            child_nodes = [item for item in child_graph.get("nodes", []) if isinstance(item, dict)]
            if len(child_nodes) > int(cfg.get("max_child_nodes", 0)):
                raise ConflictError("Subworkflow exceeds fixed max_child_nodes")
            requested_budget = cfg.get("budget_limit")
            if requested_budget is not None and (not isinstance(requested_budget, (int, float)) or requested_budget < 0):
                raise ConflictError("Subworkflow budget_limit is invalid")
            inherited_budget = (run.input_snapshot or {}).get("budget_limit")
            if isinstance(inherited_budget, (int, float)) and isinstance(requested_budget, (int, float)) and requested_budget > inherited_budget:
                raise ForbiddenError("Subworkflow cannot increase the parent budget")
            child = WorkflowRunModel(run_id=uuid.uuid4(), workflow_revision_id=child_id, compiled_plan_id=child_plan.plan_id,
                owner_scope=run.owner_scope,
                input_snapshot={**dict(cfg.get("input_mapping", {})), "subworkflow_parent": {"run_id": str(run.run_id), "node_instance_id": node.node_instance_id}, "budget_limit": requested_budget if requested_budget is not None else inherited_budget},
                status=RunStatus.QUEUED, created_at=datetime.now(timezone.utc))
            session.add(child)
            session.flush()
            targets = {str(edge.get("target", "")) for edge in child_graph.get("edges", []) if isinstance(edge, dict)}
            for child_node in child_nodes:
                session.add(NodeRunModel(node_run_id=uuid.uuid4(), run_id=child.run_id, node_instance_id=str(child_node.get("id", "")),
                    node_type_id=str(child_node.get("type", "")), status=NodeRunStatus.READY if str(child_node.get("id", "")) not in targets else NodeRunStatus.PENDING))
            session.add(SubworkflowModel(subworkflow_id=uuid.uuid4(), run_id=run.run_id, node_instance_id=node.node_instance_id,
                child_run_id=child.run_id, parent_node_instance_id=node.node_instance_id, status="running", config=cfg))
            child.status = RunStatus.RUNNING
            session.flush()
            self._sql_schedule_ready(session, child, graph=child_graph)

    @staticmethod
    def _sql_validate_subworkflow_mappings(cfg: dict[str, Any]) -> None:
        """Re-validate the compiled typed-port contract at execution time.

        Plans normally arrive through ``WorkflowCompiler``.  The runtime
        still validates them because persisted plans may be imported or
        migrated, and a direct service caller must not turn a typed port
        mapping into arbitrary JSON just by bypassing the HTTP compiler route.
        """
        for mapping_name in ("input_mapping", "output_mapping"):
            mappings = cfg.get(mapping_name, {})
            if not isinstance(mappings, dict):
                raise ConflictError(f"Subworkflow {mapping_name} is not typed")
            for port_name, binding in mappings.items():
                if not isinstance(binding, dict):
                    raise ConflictError(f"Subworkflow {mapping_name}.{port_name} is not typed")
                required = {"source_port", "target_port", "schema_id", "schema_version"}
                if not required <= set(binding):
                    raise ConflictError(
                        f"Subworkflow {mapping_name}.{port_name} requires source_port, target_port, schema_id and schema_version"
                    )
                if any(not isinstance(binding[key], str) or not binding[key].strip() for key in ("source_port", "target_port", "schema_id")):
                    raise ConflictError(f"Subworkflow {mapping_name}.{port_name} has an invalid typed port")
                if any(str(value).lower() == "latest" for value in binding.values()):
                    raise ConflictError(f"Subworkflow {mapping_name}.{port_name} cannot reference latest")
                if not isinstance(binding["schema_version"], int) or binding["schema_version"] < 1:
                    raise ConflictError(f"Subworkflow {mapping_name}.{port_name} schema_version must be positive")
                if mapping_name == "output_mapping":
                    source_node_id = binding.get("source_node_id")
                    if not isinstance(source_node_id, str) or not source_node_id.strip():
                        raise ConflictError(f"Subworkflow output_mapping.{port_name} requires source_node_id")

    @staticmethod
    def _sql_validate_subworkflow_inputs(session: Any, run: WorkflowRunModel, cfg: dict[str, Any]) -> None:
        """Accept only fixed same-owner Artifacts or granted Resource revisions."""
        mappings = cfg.get("input_mapping", {})
        if not isinstance(mappings, dict):
            raise ConflictError("Subworkflow input_mapping is not typed")
        for port_name, binding in mappings.items():
            if not isinstance(binding, dict):
                raise ConflictError(f"Subworkflow input {port_name} is not typed")
            raw_artifact = binding.get("artifact_version_id")
            if raw_artifact:
                try:
                    artifact_id = uuid.UUID(str(raw_artifact))
                except (TypeError, ValueError) as exc:
                    raise ConflictError(f"Subworkflow input {port_name} has an invalid ArtifactVersion") from exc
                artifact = session.get(ArtifactVersionModel, artifact_id)
                if artifact is None or artifact.owner_scope != run.owner_scope:
                    raise ForbiddenError("Cross-owner ArtifactVersion requires an explicit Resource grant")
            raw_resource = binding.get("resource_revision_id")
            if raw_resource:
                try:
                    revision_id = uuid.UUID(str(raw_resource))
                except (TypeError, ValueError) as exc:
                    raise ConflictError(f"Subworkflow input {port_name} has an invalid ResourceRevision") from exc
                revision = session.get(ResourceRevisionModel, revision_id)
                resource = session.get(ResourceModel, revision.resource_id) if revision is not None else None
                granted = session.scalar(select(ResourceGrantSnapshotModel.grant_snapshot_id).where(
                    ResourceGrantSnapshotModel.resource_revision_id == revision_id,
                    ResourceGrantSnapshotModel.grantee_scope == run.owner_scope,
                    ResourceGrantSnapshotModel.status == "active",
                ))
                if resource is None or (resource.owner_scope != run.owner_scope and granted is None):
                    raise ForbiddenError("Subworkflow ResourceRevision is not owned or granted to the parent")

    @staticmethod
    def _sql_validate_subworkflow_capabilities(session: Any, run: WorkflowRunModel, child_plan: CompiledExecutionPlanModel) -> None:
        """Child plan may only reduce the parent plan's frozen executor scope."""
        parent_plan = session.get(CompiledExecutionPlanModel, run.compiled_plan_id)
        if parent_plan is None:
            # Direct service tests may construct a plan in memory.  The public
            # run API never takes this branch because it requires a persisted
            # successful parent plan.
            return

        def capabilities(plan_json: dict[str, Any]) -> set[str]:
            values = {str(value) for value in plan_json.get("capability_snapshots", []) if value}
            values.update(str(value) for value in (plan_json.get("executor_refs", {}) or {}).values() if value)
            graph = plan_json.get("resolved_graph", {})
            for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
                if not isinstance(node, dict):
                    continue
                data = node.get("data") if isinstance(node.get("data"), dict) else {}
                config = node.get("config") if isinstance(node.get("config"), dict) else data.get("config", {})
                if isinstance(config, dict):
                    for key in ("provider_ref", "tool_revision_id"):
                        if config.get(key):
                            values.add(str(config[key]))
            return values

        parent_capabilities = capabilities(parent_plan.plan_json if isinstance(parent_plan.plan_json, dict) else {})
        child_capabilities = capabilities(child_plan.plan_json if isinstance(child_plan.plan_json, dict) else {})
        expanded = child_capabilities - parent_capabilities
        if expanded:
            raise ForbiddenError(f"Subworkflow expands frozen capability scope: {', '.join(sorted(expanded))}")

    def _sql_aggregate_run(self, session: Any, run: WorkflowRunModel) -> None:
        rows = list(session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id)))
        if any(row.status in {NodeRunStatus.READY, NodeRunStatus.RUNNING, NodeRunStatus.PENDING} for row in rows):
            run.status = RunStatus.RUNNING
        elif any(row.status == NodeRunStatus.WAITING_USER for row in rows):
            run.status = RunStatus.WAITING_USER
        elif any(row.status == NodeRunStatus.FAILED for row in rows):
            run.status = RunStatus.FAILED
        elif rows and all(row.status in {NodeRunStatus.COMPLETED, NodeRunStatus.SKIPPED} for row in rows):
            run.status = RunStatus.COMPLETED
        # A terminal child run settles its parent SubworkflowCall in the same
        # database transaction, so no polling endpoint can manufacture it.
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            for binding in session.scalars(select(SubworkflowModel).where(SubworkflowModel.child_run_id == run.run_id)):
                parent_run = session.get(WorkflowRunModel, binding.run_id)
                parent_node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == binding.run_id, NodeRunModel.node_instance_id == binding.node_instance_id))
                if parent_run is None or parent_node is None:
                    continue
                binding.status = run.status.value
                if run.status == RunStatus.COMPLETED:
                    output_mapping = (binding.config or {}).get("output_mapping", {})
                    pinned_outputs: dict[str, list[str]] = {}
                    if not isinstance(output_mapping, dict):
                        raise ConflictError("Subworkflow output_mapping is not a typed mapping")
                    for parent_port, mapping in output_mapping.items():
                        if not isinstance(mapping, dict):
                            raise ConflictError("Subworkflow output mapping is not typed")
                        source_node_id = str(mapping.get("source_node_id", ""))
                        if not source_node_id:
                            raise ConflictError("Subworkflow output mapping requires source_node_id")
                        child_node = session.scalar(select(NodeRunModel).where(
                            NodeRunModel.run_id == run.run_id,
                            NodeRunModel.node_instance_id == source_node_id,
                            NodeRunModel.status == NodeRunStatus.COMPLETED,
                        ))
                        if child_node is None:
                            raise ConflictError(f"Subworkflow output source {source_node_id} is not completed")
                        artifacts = list(session.scalars(
                            select(ProviderOutputBindingModel.output_artifact_version_id)
                            .join(ProviderInvocationRecordModel, ProviderInvocationRecordModel.record_id == ProviderOutputBindingModel.record_id)
                            .join(ProviderInvocationAttemptModel, ProviderInvocationAttemptModel.provider_attempt_id == ProviderInvocationRecordModel.provider_attempt_id)
                            .join(NodeRunAttemptModel, NodeRunAttemptModel.attempt_id == ProviderInvocationAttemptModel.node_run_attempt_id)
                            .where(NodeRunAttemptModel.node_run_id == child_node.node_run_id)
                            .order_by(ProviderOutputBindingModel.output_index)
                        ))
                        if not artifacts:
                            child_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == child_node.node_run_id).order_by(NodeRunAttemptModel.execution_epoch.desc()))
                            map_output = dict(child_attempt.fixed_input or {}).get("map_output") if child_attempt is not None else None
                            if isinstance(map_output, dict) and map_output.get("artifact_version_id"):
                                artifacts = [uuid.UUID(str(map_output["artifact_version_id"]))]
                        if not artifacts:
                            raise ConflictError(f"Subworkflow output source {source_node_id} has no fixed ArtifactVersion")
                        pinned_outputs[str(parent_port)] = [str(item) for item in artifacts]
                    parent_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == parent_node.node_run_id).order_by(NodeRunAttemptModel.execution_epoch.desc()))
                    if parent_attempt is None:
                        parent_attempt = NodeRunAttemptModel(attempt_id=uuid.uuid4(), node_run_id=parent_node.node_run_id,
                            attempt_number=1, execution_epoch=1, fixed_input={}, status=AttemptStatus.COMPLETED,
                            completed_at=datetime.now(timezone.utc))
                        session.add(parent_attempt)
                    fixed_parent_input = dict(parent_attempt.fixed_input or {})
                    fixed_parent_input["subworkflow_output"] = pinned_outputs
                    parent_attempt.fixed_input, parent_attempt.status, parent_attempt.completed_at = fixed_parent_input, AttemptStatus.COMPLETED, datetime.now(timezone.utc)
                    parent_node.status = NodeRunStatus.COMPLETED
                else:
                    parent_node.status = NodeRunStatus.FAILED if run.status == RunStatus.FAILED else NodeRunStatus.CANCELLED
                self._sql_schedule_ready(session, parent_run)

    def _sql_dispatch_provider(
        self, attempt_id: uuid.UUID, provider_id: str, model_id: str,
        idempotency_key: str, request_body_hash: str,
    ) -> tuple[ProviderInvocationAttempt, OutboxEvent]:
        """Insert invocation and dispatch event in one database transaction."""
        assert self._session_factory is not None
        with self._session_factory.begin() as session:
            attempt = self._sql_required(session, NodeRunAttemptModel, attempt_id, "NodeRunAttempt")
            prior = session.scalar(select(ProviderInvocationAttemptModel).where(ProviderInvocationAttemptModel.idempotency_key == idempotency_key))
            if prior is not None:
                # Retrying a caller after commit is idempotent and does not enqueue twice.
                event = session.scalar(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == prior.provider_attempt_id, OutboxEventModel.event_type == "provider.dispatch"))
                if event is None:
                    raise ConflictError("幂等 Provider 调用缺少 dispatch outbox")
                return self._provider_attempt_schema(prior), self._outbox_schema(event)
            provider = ProviderInvocationAttemptModel(
                provider_attempt_id=uuid.uuid4(), node_run_attempt_id=attempt_id,
                provider_id=provider_id, model_id=model_id, idempotency_key=idempotency_key,
                request_body_hash=request_body_hash, status=AttemptStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )
            event = OutboxEventModel(
                event_id=uuid.uuid4(), aggregate_type="provider_invocation",
                aggregate_id=provider.provider_attempt_id, event_type="provider.dispatch",
                payload={"provider_id": provider_id, "model_id": model_id, "idempotency_key": idempotency_key},
                purpose="provider_dispatch", created_at=datetime.now(timezone.utc),
            )
            session.add_all([provider, event])
            attempt.status = AttemptStatus.WAITING_EXTERNAL
            session.flush()
            return self._provider_attempt_schema(provider), self._outbox_schema(event)

    def _sql_record_provider_result(
        self, provider_attempt_id: uuid.UUID, model_version: str, response_fingerprint: str,
        usage: dict[str, Any], actual_cost: float, artifact_ids: list[uuid.UUID], current_epoch: int | None,
    ) -> tuple[ProviderInvocationRecord, OutboxEvent]:
        """Atomically persist record, all output bindings, cost and publish event."""
        assert self._session_factory is not None
        with self._session_factory.begin() as session:
            provider = self._sql_required(session, ProviderInvocationAttemptModel, provider_attempt_id, "ProviderInvocationAttempt")
            attempt = self._sql_required(session, NodeRunAttemptModel, provider.node_run_attempt_id, "NodeRunAttempt")
            if current_epoch is not None and attempt.execution_epoch != current_epoch:
                raise ConflictError("执行纪元过期，结果被拒绝")
            existing = session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider_attempt_id))
            if existing is not None:
                event = session.scalar(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == provider_attempt_id, OutboxEventModel.event_type == "provider.result"))
                if event is None:
                    raise ConflictError("幂等 Provider 结果缺少 result outbox")
                return self._provider_record_schema(existing), self._outbox_schema(event)
            missing = session.scalar(select(func.count()).select_from(ArtifactVersionModel).where(ArtifactVersionModel.artifact_version_id.in_(artifact_ids))) if artifact_ids else 0
            if artifact_ids and missing != len(set(artifact_ids)):
                raise NotFoundError("ArtifactVersion", "provider output artifact")
            now = datetime.now(timezone.utc)
            record = ProviderInvocationRecordModel(
                record_id=uuid.uuid4(), provider_attempt_id=provider_attempt_id,
                provider_id=provider.provider_id, model_id=provider.model_id, model_version=model_version,
                idempotency_key=provider.idempotency_key, request_body_hash=provider.request_body_hash,
                response_fingerprint=response_fingerprint, usage=usage, actual_cost=actual_cost,
                started_at=now, completed_at=now,
            )
            event = OutboxEventModel(
                event_id=uuid.uuid4(), aggregate_type="provider_invocation", aggregate_id=provider_attempt_id,
                event_type="provider.result", payload={"record_id": str(record.record_id), "status": "completed", "cost": actual_cost},
                purpose="result_publish", created_at=now,
            )
            session.add_all([record, event])
            for index, artifact_id in enumerate(artifact_ids):
                session.add(ProviderOutputBindingModel(
                    binding_id=uuid.uuid4(), record_id=record.record_id,
                    output_artifact_version_id=artifact_id, output_index=index,
                ))
            provider.status = AttemptStatus.COMPLETED
            attempt.status, attempt.completed_at = AttemptStatus.COMPLETED, now
            node_run = self._sql_required(session, NodeRunModel, attempt.node_run_id, "NodeRun")
            node_run.status = NodeRunStatus.COMPLETED
            # Parent Subworkflow aggregation reads NodeRun and output binding
            # rows immediately below.  These models intentionally have no ORM
            # relationships, so make their FK/state writes visible before the
            # scheduler performs its in-transaction query chain.
            session.flush()
            self._sql_schedule_ready(session, self._sql_required(session, WorkflowRunModel, node_run.run_id, "WorkflowRun"))
            session.flush()
            return self._provider_record_schema(record), self._outbox_schema(event)

    def publish_provider_json_outputs(
        self, provider_attempt_id: uuid.UUID, *, owner_scope: str, schema_id: str,
        schema_version: int, outputs: list[dict[str, Any]], model_version: str,
        response_fingerprint: str, usage: dict[str, Any], actual_cost: float,
        current_epoch: int | None = None,
    ) -> tuple[ProviderInvocationRecord, OutboxEvent, list[uuid.UUID]]:
        """Atomically create output artifacts, record, bindings and outbox.

        This is the only successful JSON-provider publication path for Agent
        and Recipe workers. Callers must validate output shape before entry.
        """
        assert self._session_factory is not None
        if not outputs:
            raise ValidationError_("Provider result must contain at least one typed output")
        with self._session_factory.begin() as session:
            provider = self._sql_required(session, ProviderInvocationAttemptModel, provider_attempt_id, "ProviderInvocationAttempt")
            attempt = self._sql_required(session, NodeRunAttemptModel, provider.node_run_attempt_id, "NodeRunAttempt")
            if current_epoch is not None and attempt.execution_epoch != current_epoch:
                raise ConflictError("执行纪元过期，结果被拒绝")
            existing = session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider_attempt_id))
            if existing is not None:
                event = session.scalar(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == provider_attempt_id, OutboxEventModel.event_type == "provider.result"))
                ids = list(session.scalars(select(ProviderOutputBindingModel.output_artifact_version_id).where(ProviderOutputBindingModel.record_id == existing.record_id).order_by(ProviderOutputBindingModel.output_index)))
                if event is None:
                    raise ConflictError("幂等 Provider 结果缺少 result outbox")
                return self._provider_record_schema(existing), self._outbox_schema(event), ids
            now = datetime.now(timezone.utc)
            artifact_ids: list[uuid.UUID] = []
            for output in outputs:
                artifact_id = uuid.uuid4()
                version_id = uuid.uuid4()
                session.add(ArtifactVersionModel(artifact_version_id=version_id, artifact_id=artifact_id, schema_id=schema_id,
                    schema_version=schema_version, owner_scope=owner_scope, content_json=output,
                    content_hash=hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest(), content_uri="", blob_uri="", metadata_json={}, created_at=now))
                artifact_ids.append(version_id)
            record = ProviderInvocationRecordModel(record_id=uuid.uuid4(), provider_attempt_id=provider_attempt_id,
                provider_id=provider.provider_id, model_id=provider.model_id, model_version=model_version,
                idempotency_key=provider.idempotency_key, request_body_hash=provider.request_body_hash,
                response_fingerprint=response_fingerprint, usage=usage, actual_cost=actual_cost, started_at=now, completed_at=now)
            event = OutboxEventModel(event_id=uuid.uuid4(), aggregate_type="provider_invocation", aggregate_id=provider_attempt_id,
                event_type="provider.result", payload={"record_id": str(record.record_id), "status": "completed", "cost": actual_cost}, purpose="result_publish", created_at=now)
            session.add_all([record, event])
            for index, artifact_id in enumerate(artifact_ids):
                session.add(ProviderOutputBindingModel(binding_id=uuid.uuid4(), record_id=record.record_id, output_artifact_version_id=artifact_id, output_index=index))
            provider.status = AttemptStatus.COMPLETED
            attempt.status, attempt.completed_at = AttemptStatus.COMPLETED, now
            node_run = self._sql_required(session, NodeRunModel, attempt.node_run_id, "NodeRun")
            node_run.status = NodeRunStatus.COMPLETED
            session.flush()
            self._sql_schedule_ready(session, self._sql_required(session, WorkflowRunModel, node_run.run_id, "WorkflowRun"))
            session.flush()
            return self._provider_record_schema(record), self._outbox_schema(event), artifact_ids

    @staticmethod
    def _run_schema(row: Any) -> WorkflowRun:
        kind, raw_id = row.owner_scope.split(":", 1)
        return WorkflowRun(run_id=row.run_id, workflow_revision_id=row.workflow_revision_id, compiled_plan_id=row.compiled_plan_id, owner_scope=OwnerScope(kind=kind, id=uuid.UUID(raw_id)), input_snapshot=row.input_snapshot or {}, status=row.status, created_at=row.created_at)

    @staticmethod
    def _attempt_schema(row: Any) -> NodeRunAttempt:
        return NodeRunAttempt(attempt_id=row.attempt_id, node_run_id=row.node_run_id, attempt_number=row.attempt_number, execution_epoch=row.execution_epoch, lease_id=row.lease_id, lease_expires_at=row.lease_expires_at, fixed_input=row.fixed_input or {}, status=row.status, started_at=row.started_at, completed_at=row.completed_at)

    @staticmethod
    def _provider_attempt_schema(row: Any) -> ProviderInvocationAttempt:
        return ProviderInvocationAttempt(provider_attempt_id=row.provider_attempt_id, node_run_attempt_id=row.node_run_attempt_id, provider_id=row.provider_id, model_id=row.model_id, idempotency_key=row.idempotency_key, request_body_hash=row.request_body_hash, status=row.status)

    @staticmethod
    def _provider_record_schema(row: Any) -> ProviderInvocationRecord:
        return ProviderInvocationRecord(record_id=row.record_id, provider_attempt_id=row.provider_attempt_id, provider_id=row.provider_id, model_id=row.model_id, model_version=row.model_version, idempotency_key=row.idempotency_key, request_body_hash=row.request_body_hash, response_fingerprint=row.response_fingerprint, usage=row.usage or {}, actual_cost=row.actual_cost, started_at=row.started_at, completed_at=row.completed_at)

    @staticmethod
    def _task_binding_schema(row: Any) -> WorkflowTaskBinding:
        return WorkflowTaskBinding(
            binding_id=row.binding_id, node_run_attempt_id=row.node_run_attempt_id,
            provider_attempt_id=row.provider_attempt_id, provider_task_id=row.provider_task_id,
            task_status=row.task_status,
        )

    @staticmethod
    def _outbox_schema(row: Any) -> OutboxEvent:
        return OutboxEvent(event_id=row.event_id, aggregate_type=row.aggregate_type, aggregate_id=row.aggregate_id, event_type=row.event_type, payload=row.payload or {}, purpose=row.purpose, created_at=row.created_at, published_at=row.published_at, retry_count=row.retry_count)

    @staticmethod
    def _json_refs(refs: list[Any]) -> list[dict[str, Any]]:
        return [ref.model_dump(mode="json") if hasattr(ref, "model_dump") else ref for ref in refs]

    @staticmethod
    def _human_task_schema(row: Any) -> HumanTaskRecord:
        # Early RequestInput rows used an internal discriminator in this
        # shared enum column.  Keep them readable during a rolling upgrade;
        # new rows use advisory and owner_layer=agent.
        policy_strength = row.policy_strength
        if policy_strength == "agent_request_input":
            policy_strength = "advisory"
        return HumanTaskRecord(
            task_id=row.task_id, task_kind=row.task_kind, owner_layer=row.owner_layer,
            owner_revision_id=row.owner_revision_id, run_id=row.run_id, node_run_id=row.node_run_id,
            attempt_id=row.attempt_id, input_snapshot_refs=row.input_snapshot_refs or [],
            policy_strength=policy_strength, schema_ref=row.schema_ref,
            timeout_policy=row.timeout_policy or {}, status=row.status,
            task_version=row.task_version, created_at=row.created_at,
        )

    # ------------------------------------------------------------------
    # In-memory internal getters
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
