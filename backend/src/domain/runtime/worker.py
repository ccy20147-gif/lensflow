"""TF-WF-006 — Runtime worker entrypoint (PG-backed, outbox-emitting).

The worker owns the run/attempt/provider-attempt lifecycle beyond the API:

  * ``start_run``           — drive a WorkflowRun from QUEUED → RUNNING and
                              emit a ``run.started`` outbox event.
  * ``claim_next_attempt``  — atomically lease the next READY/PENDING
                              attempt with epoch/fencing semantics.
  * ``record_provider_result`` — append a ProviderInvocationRecord, emit
                              ``provider.result``, mark the attempt and its
                              parent node run terminal.
  * ``recover_pending``     — restart-time scan over UNKNOWN / WAITING_EXTERNAL
                              attempts and stale leases.

Every mutating method writes to PostgreSQL through the ``session_factory``
injected at construction time and emits its outbox event in the same
transaction as the business row, so dispatch and result state cannot be
split across a network failure and a database failure.

The worker reuses ``RuntimeService`` for the per-row SQL helpers so
epoch/fencing semantics match what ``runtime_service.py`` already provides.
"""
from __future__ import annotations

import uuid
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.runtime.runtime_service import RuntimeService
from src.infra.db.models import (
    NodeRunAttemptModel,
    NodeRunModel,
    OutboxEventModel,
    ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    WorkflowRunModel,
    WorkflowTaskBindingModel,
    ForEachRunModel,
    MapItemRunModel,
    FoldCheckpointModel,
    ArtifactVersionModel,
    MediaRecipeDefinitionModel,
    MediaRecipeRevisionModel,
    SubworkflowModel,
)
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown
from src.domain.agent.tool_broker import ToolBroker
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus, RunStatus
from src.schemas.models import (
    NodeRunAttempt,
    OutboxEvent,
    OwnerScope,
    ProviderInvocationRecord,
    WorkflowRun,
)


# Default lease duration when an attempt is claimed.
DEFAULT_LEASE_DURATION = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderResultReceipt:
    """Persisted provider result, ready for downstream consumers."""

    record: ProviderInvocationRecord
    outbox: OutboxEvent


@dataclass(frozen=True)
class AttemptClaim:
    """A leased attempt ready for the worker to execute."""

    attempt: NodeRunAttempt
    node_run_id: UUID
    lease_expires_at: datetime


@dataclass(frozen=True)
class MapItemClaim:
    map_item_id: UUID
    run_id: UUID
    node_instance_id: str
    item_index: int
    item_value: dict[str, Any]


@dataclass(frozen=True)
class RecoveryReport:
    """Summary of restart-time recovery actions."""

    unknown_attempts: int
    waiting_external: int
    requeued_attempts: int


@dataclass(frozen=True)
class ReconciliationReport:
    checked: int
    completed: int
    failed: int
    pending: int


# ---------------------------------------------------------------------------
# RuntimeWorker
# ---------------------------------------------------------------------------


class RuntimeWorker:
    """PG-backed worker entrypoint for the runtime lifecycle."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        agent_invocations: AgentInvocationService | None = None,
    ) -> None:
        self._factory = session_factory or get_session_factory()
        self._runtime = RuntimeService(self._factory)
        self._lease_duration = lease_duration
        self._agent_invocations = agent_invocations

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, run_id: UUID) -> WorkflowRun:
        """Transition QUEUED → RUNNING and emit ``run.started``.

        Idempotent: re-entering a RUNNING run is a no-op (returns the current
        state without re-emitting the event).
        """
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            row = session.get(WorkflowRunModel, run_id)
            if row is None:
                raise NotFoundError("WorkflowRun", str(run_id))
            if row.status == RunStatus.RUNNING:
                # Idempotent — already started.
                return self._runtime_to_schema(row)
            if row.status != RunStatus.QUEUED:
                raise ConflictError(
                    message=f"运行 {run_id} 状态为 {row.status}，不能启动"
                )
            row.status = RunStatus.RUNNING
            event = OutboxEventModel(
                event_id=uuid.uuid4(),
                aggregate_type="workflow_run",
                aggregate_id=row.run_id,
                event_type="run.started",
                payload={
                    "workflow_revision_id": str(row.workflow_revision_id),
                    "compiled_plan_id": str(row.compiled_plan_id),
                },
                purpose="run_started",
                created_at=now,
            )
            session.add(event)
            session.flush()
            session.expunge(row)
            return self._runtime_to_schema(row)

    # ------------------------------------------------------------------
    # Attempt claiming
    # ------------------------------------------------------------------

    def claim_next_attempt(
        self,
        worker_id: str,
        *,
        run_id: UUID | None = None,
        node_type_ids: set[str] | None = None,
    ) -> AttemptClaim | None:
        """Atomically lease the next ready attempt for ``worker_id``.

        Returns ``None`` when no PENDING / READY attempt is available.  The
        ``UPDATE`` predicate is restricted to terminal lease state
        (``lease_id IS NULL OR lease_expires_at < now``) so two workers
        cannot both claim the same row.  Fencing is enforced at completion
        via ``execution_epoch`` rather than the lease clock.
        """
        now = datetime.now(timezone.utc)
        lease_expires_at = now + self._lease_duration
        with self._factory.begin() as session:
            # 1. Find a candidate attempt whose parent node run is in a
            #    claimable state and whose lease is not held by an active
            #    worker.  ``with_for_update(skip_locked=True)`` lets
            #    concurrent workers each pick a distinct row.
            predicates = [
                NodeRunAttemptModel.status == AttemptStatus.PENDING,
                NodeRunModel.node_type_id != "map_item",
                NodeRunModel.status.in_([NodeRunStatus.PENDING, NodeRunStatus.READY, NodeRunStatus.RUNNING]),
                NodeRunModel.run_id.in_(select(WorkflowRunModel.run_id).where(WorkflowRunModel.status == RunStatus.RUNNING)),
            ]
            if run_id is not None:
                predicates.append(NodeRunModel.run_id == run_id)
            if node_type_ids is not None:
                if not node_type_ids:
                    return None
                predicates.append(NodeRunModel.node_type_id.in_(node_type_ids))
            stmt = (
                select(NodeRunAttemptModel)
                .join(NodeRunModel, NodeRunModel.node_run_id == NodeRunAttemptModel.node_run_id)
                .where(*predicates)
                # Attempt rows do not have a created_at column; UUID is a
                # deterministic tie-breaker once readiness has been persisted.
                .order_by(NodeRunAttemptModel.attempt_id.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            candidate = session.scalar(stmt)
            if candidate is None:
                return None
            # 2. Update with epoch fencing: only lease when the lease is
            #    free or already expired.  ``UPDATE ... WHERE`` with the
            #    predicate above guarantees a single worker wins.
            claim_stmt = (
                update(NodeRunAttemptModel)
                .where(
                    NodeRunAttemptModel.attempt_id == candidate.attempt_id,
                    NodeRunAttemptModel.status == AttemptStatus.PENDING,
                )
                .values(
                    status=AttemptStatus.LEASED,
                    lease_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    started_at=now,
                )
            )
            result = session.execute(claim_stmt)
            if result.rowcount != 1:
                # Lost the race; let the next poll pick another attempt.
                return None
            # 3. Drive the parent node run to RUNNING for visibility.
            session.execute(
                update(NodeRunModel)
                .where(NodeRunModel.node_run_id == candidate.node_run_id)
                .values(status=NodeRunStatus.RUNNING)
            )
            event = OutboxEventModel(
                event_id=uuid.uuid4(),
                aggregate_type="node_run_attempt",
                aggregate_id=candidate.attempt_id,
                event_type="attempt.leased",
                payload={
                    "worker_id": worker_id,
                    "execution_epoch": candidate.execution_epoch,
                    "lease_expires_at": lease_expires_at.isoformat(),
                },
                purpose="attempt_leased",
                created_at=now,
            )
            session.add(event)
            session.flush()
            session.refresh(candidate)
            attempt_schema = self._attempt_to_schema(candidate)
            return AttemptClaim(
                attempt=attempt_schema,
                node_run_id=candidate.node_run_id,
                lease_expires_at=lease_expires_at,
            )

    def execute_business_attempt(self, attempt_id: UUID) -> list[UUID]:
        """Execute a registered TF-WF-010 node from one durable attempt."""
        from src.domain.workflow.business_node_service import BusinessNodeService

        outputs = BusinessNodeService(self._factory).execute_attempt(attempt_id)
        return [UUID(str(row.artifact_version_id)) for row in outputs]

    def fail_attempt(self, attempt_id: UUID) -> NodeRunAttempt:
        """Expose terminal failure convergence to the worker process only."""
        return self._runtime.fail_attempt(attempt_id)

    def execute_attempt(self, attempt_id: UUID) -> dict[str, Any]:
        """Execute a leased fixed-plan attempt by its registered node type.

        Agent nodes never receive a browser-supplied revision or inputs here:
        both come from the persisted NodeRunAttempt created by the scheduler.
        This is the missing bridge between an explicit Agent DAG and the
        durable AtlasCloud/outbox invocation contract.
        """
        with self._factory() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            if attempt is None:
                raise NotFoundError("NodeRunAttempt", str(attempt_id))
            node = session.get(NodeRunModel, attempt.node_run_id)
            run = session.get(WorkflowRunModel, node.run_id) if node is not None else None
            if node is None or run is None:
                raise NotFoundError("WorkflowRun", str(attempt_id))
            if attempt.status not in {AttemptStatus.LEASED, AttemptStatus.RUNNING}:
                raise ConflictError("Worker may execute only a currently leased attempt")
            node_type, fixed_input = node.node_type_id, dict(attempt.fixed_input or {})
            epoch = attempt.execution_epoch
            owner_scope = run.owner_scope

        if node_type == "agent_invoke" or node_type.startswith("agent.invoke."):
            raw_revision = fixed_input.get("agent_revision_id")
            try:
                revision_id = UUID(str(raw_revision))
                kind, raw_owner_id = owner_scope.split(":", 1)
                owner = OwnerScope(kind=kind, id=UUID(raw_owner_id))
            except (TypeError, ValueError) as exc:
                self._runtime.fail_attempt(attempt_id)
                raise ValidationError_("Agent worker attempt lacks a valid pinned revision or owner") from exc
            if node_type.startswith("agent.invoke.") and node_type != f"agent.invoke.{revision_id}":
                self._runtime.fail_attempt(attempt_id)
                raise ValidationError_("Agent node type does not match its pinned revision")
            internal_keys = {
                "agent_revision_id", "committed_resource_refs",
                "upstream_artifact_refs", "fallback_for_node_ids",
            }
            # Preserve the original fixed run inputs (including a persisted
            # RequestInput answer on resume) while keeping scheduler metadata
            # explicit and separate from user-controlled fields.
            typed_inputs = {
                key: value for key, value in fixed_input.items()
                if key not in internal_keys
            }
            typed_inputs["upstream_artifact_refs"] = list(fixed_input.get("upstream_artifact_refs", []))
            typed_inputs["committed_resource_refs"] = list(fixed_input.get("committed_resource_refs", []))
            invocation = self._agent_invocations or AgentInvocationService(self._factory)
            try:
                result = invocation.execute(
                    agent_revision_id=revision_id,
                    owner_scope=owner,
                    node_run_attempt_id=attempt_id,
                    typed_inputs=typed_inputs,
                    idempotency_key=f"agent-run:{attempt_id}:{epoch}",
                )
            except (ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_):
                # AgentInvocationService marks schema failures itself.  Missing
                # credentials and malformed fixed bindings must also settle the
                # node rather than leaving a leased attempt indefinitely.
                self._runtime.fail_attempt(attempt_id)
                raise
            response = {
                "kind": "agent_invoke", "status": result["status"],
                "agent_revision_id": str(revision_id),
                "artifact_version_ids": [str(value) for value in result.get("artifact_version_ids", [])],
                "provider_attempt_id": str(result["provider_attempt_id"]) if result.get("provider_attempt_id") else None,
            }
            # Expanded Map/Fold children retain the same fixed Agent contract
            # as top-level nodes, then atomically converge into their parent.
            raw_map_item = fixed_input.get("map_item_id")
            if raw_map_item and result["status"] == "completed":
                try:
                    self.complete_map_item(
                        UUID(str(raw_map_item)),
                        {"agent_artifact_version_ids": response["artifact_version_ids"]},
                        expected_epoch=epoch,
                    )
                except (TypeError, ValueError) as exc:
                    raise ValidationError_("Agent map item has an invalid fixed map_item_id") from exc
            return response

        if node_type == "join":
            return self._execute_join_attempt(attempt_id, epoch=epoch)

        if node_type.startswith("media.recipe."):
            raw_revision = fixed_input.get("media_recipe_revision_id")
            try:
                revision_id = UUID(str(raw_revision))
            except (TypeError, ValueError) as exc:
                self._runtime.fail_attempt(attempt_id)
                raise ValidationError_("Media Recipe worker attempt lacks a pinned revision") from exc
            if node_type != f"media.recipe.{revision_id}":
                self._runtime.fail_attempt(attempt_id)
                raise ValidationError_("Media Recipe node type does not match its pinned revision")
            with self._factory() as session:
                revision = session.get(MediaRecipeRevisionModel, revision_id)
                definition = session.get(MediaRecipeDefinitionModel, revision.recipe_id) if revision else None
                if revision is None or definition is None or definition.owner_scope != owner_scope or revision.status != "active":
                    self._runtime.fail_attempt(attempt_id)
                    raise ValidationError_("Media Recipe worker requires an active owner-scoped frozen revision")
                frozen_body = dict(revision.body or {})
            from src.domain.recipe.recipe_runtime import RecipeRuntimeService
            children = RecipeRuntimeService(self._factory).materialize(
                parent_attempt_id=attempt_id,
                body=frozen_body,
                inputs={key: value for key, value in fixed_input.items() if key not in {"media_recipe_revision_id", "upstream_artifact_refs", "committed_resource_refs"}},
            )
            return {"kind": "media_recipe_invoke", "status": "waiting_external", "media_recipe_revision_id": str(revision_id), "operator_attempt_ids": [str(value) for value in children]}

        if node_type.startswith("recipe."):
            from src.domain.recipe.recipe_runtime import RecipeRuntimeService
            runtime = RecipeRuntimeService(self._factory)
            step = dict(fixed_input.get("operator") or {})
            if str(step.get("operator")) in {"atlas_llm", "atlas_image", "atlas_video"}:
                result = runtime.dispatch_external(attempt_id, adapter=AtlasCloudAdapter(), idempotency_key=f"recipe:{attempt_id}:{epoch}")
                return {"kind": "media_recipe_operator", **{key: str(value) if isinstance(value, UUID) else value for key, value in result.items()}}
            parent_id = UUID(str(fixed_input["recipe_parent_attempt_id"]))
            runtime.complete_internal(attempt_id)
            # RecipeRuntimeService marks a completed parent when all private
            # children settle. Re-enter RuntimeService only for that parent so
            # outer workflow dependencies are scheduled; the children remain
            # invisible to the canvas graph.
            with self._factory() as session:
                parent = session.get(NodeRunAttemptModel, parent_id)
                parent_done = parent is not None and parent.status == AttemptStatus.COMPLETED
            if parent_done:
                self._runtime.complete_attempt(parent_id)
            return {"kind": "media_recipe_operator", "status": "completed", "recipe_parent_attempt_id": str(parent_id)}

        outputs = self.execute_business_attempt(attempt_id)
        return {"kind": "business_node", "status": "completed" if outputs else "waiting_user", "artifact_version_ids": [str(value) for value in outputs]}

    def _execute_join_attempt(self, attempt_id: UUID, *, epoch: int) -> dict[str, Any]:
        """Publish a stable Join artifact from fixed predecessor outputs."""
        with self._factory.begin() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            run = session.get(WorkflowRunModel, node.run_id) if node else None
            if attempt is None or node is None or run is None:
                raise NotFoundError("NodeRunAttempt", str(attempt_id))
            if attempt.execution_epoch != epoch or attempt.status not in {AttemptStatus.LEASED, AttemptStatus.RUNNING}:
                raise ConflictError("Join attempt is no longer owned by this worker")
            fixed = dict(attempt.fixed_input or {})
            refs = list(fixed.get("upstream_artifact_refs", []))
            if not all(isinstance(item, dict) and item.get("source_node_id") for item in refs):
                raise ValidationError_("Join requires fixed upstream artifact references")
            # Persisted scheduling order cannot leak worker completion timing.
            refs.sort(key=lambda item: (str(item["source_node_id"]), json.dumps(item.get("artifact_version_ids", []), sort_keys=True)))
            content = {"source_outputs": refs}
            payload = json.dumps(content, sort_keys=True, separators=(",", ":"))
            artifact = ArtifactVersionModel(
                artifact_version_id=uuid.uuid4(), artifact_id=uuid.uuid4(),
                schema_id="join_output", schema_version=1, owner_scope=run.owner_scope,
                content_json=content, content_hash=hashlib.sha256(payload.encode()).hexdigest(),
                content_uri="", blob_uri="", created_by_run_id=run.run_id,
                lineage_input_refs=refs, metadata_json={"node_instance_id": node.node_instance_id},
                created_at=datetime.now(timezone.utc),
            )
            session.add(artifact)
            fixed["map_output"] = {"artifact_version_id": str(artifact.artifact_version_id)}
            fixed["join_output_artifact_version_id"] = str(artifact.artifact_version_id)
            attempt.fixed_input, attempt.status, attempt.completed_at = fixed, AttemptStatus.COMPLETED, datetime.now(timezone.utc)
            node.status = NodeRunStatus.COMPLETED
            self._runtime._sql_schedule_ready(session, run)
            return {"kind": "join", "status": "completed", "artifact_version_ids": [str(artifact.artifact_version_id)]}

    # ------------------------------------------------------------------
    # Provider result ingestion
    # ------------------------------------------------------------------

    def record_provider_result(
        self,
        provider_attempt_id: UUID,
        *,
        model_version: str,
        response_fingerprint: str,
        usage: dict[str, Any] | None = None,
        actual_cost: float = 0.0,
        output_artifact_version_ids: list[UUID] | None = None,
        current_epoch: int | None = None,
        output_labels: list[str] | None = None,
    ) -> ProviderResultReceipt:
        """Persist a provider result, output bindings, and emit ``provider.result``.

        All writes happen in a single transaction.  Idempotent on
        ``provider_attempt_id``: a repeated call returns the existing
        record and outbox event without creating duplicates.

        ``current_epoch`` enforces fencing — a stale worker producing
        results for a superseded attempt is rejected with ``ConflictError``.
        """
        receipt = self._runtime.record_provider_result(
            provider_attempt_id,
            model_version=model_version,
            response_fingerprint=response_fingerprint,
            usage=usage,
            actual_cost=actual_cost,
            output_artifact_version_ids=output_artifact_version_ids,
            current_epoch=current_epoch,
        )
        # If output_labels were supplied, attach them to the bindings.
        if output_labels:
            with self._factory.begin() as session:
                record_row = session.scalar(
                    select(ProviderInvocationRecordModel).where(
                        ProviderInvocationRecordModel.provider_attempt_id == provider_attempt_id
                    )
                )
                if record_row is not None:
                    bindings = list(
                        session.scalars(
                            select(ProviderOutputBindingModel)
                            .where(ProviderOutputBindingModel.record_id == record_row.record_id)
                            .order_by(ProviderOutputBindingModel.output_index)
                        )
                    )
                    for binding, label in zip(bindings, output_labels):
                        if label:
                            binding.output_label = label
                    session.flush()
        return ProviderResultReceipt(record=receipt[0], outbox=receipt[1])

    def ingest_atlas_callback(self, payload: dict[str, Any]) -> ProviderResultReceipt | None:
        """Publish one signed Atlas task callback through its durable binding.

        The unique provider record is the callback dedupe fence.  A callback
        for an unbound task is rejected by the API before it reaches here.
        """
        # Webhooks nest the provider result under ``payload`` while polling
        # returns a flat prediction object. Normalize once before applying the
        # same durable task-binding and epoch fences to both paths.
        nested = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        task_id = str(payload.get("session_id") or payload.get("task_id") or payload.get("id") or payload.get("prediction_id") or "")
        if not task_id:
            raise NotFoundError("WorkflowTaskBinding", "missing provider task id")
        with self._factory() as session:
            binding = session.scalar(select(WorkflowTaskBindingModel).where(WorkflowTaskBindingModel.provider_task_id == task_id))
            if binding is None:
                raise NotFoundError("WorkflowTaskBinding", task_id)
            provider = session.get(ProviderInvocationAttemptModel, binding.provider_attempt_id)
            if provider is None or provider.provider_id != "atlascloud":
                raise ConflictError("Callback task is not an AtlasCloud invocation")
            attempt = session.get(NodeRunAttemptModel, provider.node_run_attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            run = session.get(WorkflowRunModel, node.run_id) if node else None
            if run is None:
                raise NotFoundError("WorkflowRun", task_id)
            owner_scope = run.owner_scope
            expected_epoch = int(attempt.execution_epoch)
        envelope_status = str(payload.get("status") or "").upper()
        status = str(nested.get("status") or payload.get("state") or payload.get("status") or "completed").lower()
        if envelope_status == "ERROR" and status not in {"failed", "cancelled", "canceled", "error", "timeout"}:
            status = "failed"
        if status in {"failed", "cancelled", "canceled", "error"}:
            self._runtime.fail_attempt(provider.node_run_attempt_id)
            return None
        if status in {"queued", "pending", "processing", "running", "submitted"}:
            with self._factory.begin() as session:
                bound = session.scalar(select(WorkflowTaskBindingModel).where(WorkflowTaskBindingModel.provider_task_id == task_id))
                if bound is not None:
                    bound.task_status = status
            return None
        outputs = nested.get("outputs") or payload.get("outputs") or payload.get("data") or payload.get("choices") or []
        if not isinstance(outputs, list):
            outputs = [outputs]
        typed = [item for item in outputs if isinstance(item, dict)]
        if not typed:
            raise ConflictError("Atlas callback has no typed outputs")
        record, event, _ = self._runtime.publish_provider_json_outputs(
            provider.provider_attempt_id, owner_scope=owner_scope, schema_id="provider_output", schema_version=1,
            outputs=typed, model_version=str(nested.get("model_version") or payload.get("model_version") or provider.model_id),
            response_fingerprint=str(payload.get("request_id") or payload.get("id") or task_id),
            usage=nested.get("usage") if isinstance(nested.get("usage"), dict) else (payload.get("usage") if isinstance(payload.get("usage"), dict) else {}),
            actual_cost=float(nested.get("cost") or payload.get("cost") or 0.0),
            current_epoch=expected_epoch,
        )
        with self._factory.begin() as session:
            bound = session.scalar(select(WorkflowTaskBindingModel).where(WorkflowTaskBindingModel.provider_task_id == task_id))
            if bound is not None:
                bound.task_status = "completed"
        # Recipe operators are a second-level DAG.  Callback/reconciliation
        # uses the same completion path as a synchronous recipe result so an
        # unknown submission cannot leave its dependent operators stranded.
        if attempt is not None and (attempt.fixed_input or {}).get("recipe_parent_attempt_id"):
            from src.domain.recipe.recipe_runtime import RecipeRuntimeService

            RecipeRuntimeService(self._factory).advance_completed_child(attempt.attempt_id)
        return ProviderResultReceipt(record=record, outbox=event)

    def reconcile_unknown(self, adapter: AtlasCloudAdapter | None = None) -> ReconciliationReport:
        """Poll known Atlas tasks and retain unbound UNKNOWN rows for review.

        An unbound attempt is deliberately *not* submitted again: the original
        network outcome is unknowable.  Its ``provider_reconcile`` outbox row
        is the durable manual/reconciliation queue record.
        """
        adapter = adapter or AtlasCloudAdapter()
        checked = completed = failed = pending = 0
        with self._factory() as session:
            reconcilable_ids = list(session.scalars(select(ProviderInvocationAttemptModel.provider_attempt_id).where(
                ProviderInvocationAttemptModel.status.in_([AttemptStatus.UNKNOWN, AttemptStatus.WAITING_EXTERNAL]),
                ProviderInvocationAttemptModel.provider_id == "atlascloud",
            )))
            bindings = list(session.scalars(
                select(WorkflowTaskBindingModel)
                .join(ProviderInvocationAttemptModel, ProviderInvocationAttemptModel.provider_attempt_id == WorkflowTaskBindingModel.provider_attempt_id)
                .where(ProviderInvocationAttemptModel.provider_attempt_id.in_(reconcilable_ids))
            )) if reconcilable_ids else []
            bound_ids = {item.provider_attempt_id for item in bindings}
        # Only UNKNOWN attempts without a task are ambiguous. A normal
        # WAITING_EXTERNAL submission without a binding is still awaiting the
        # sender and must not be prematurely escalated to manual review.
        with self._factory() as session:
            unbound_unknown_ids = list(session.scalars(select(ProviderInvocationAttemptModel.provider_attempt_id).where(
                ProviderInvocationAttemptModel.provider_attempt_id.in_(set(reconcilable_ids) - bound_ids),
                ProviderInvocationAttemptModel.status == AttemptStatus.UNKNOWN,
            ))) if reconcilable_ids else []
        for provider_attempt_id in unbound_unknown_ids:
            self._ensure_provider_reconcile(provider_attempt_id, "unknown_submission_without_provider_task")
            # This is observable, durable manual work rather than a silently
            # omitted UNKNOWN invocation.
            pending += 1
        for binding in bindings:
            checked += 1
            payload = adapter.get_prediction(binding.provider_task_id)
            status = str(payload.get("status") or payload.get("state") or "pending").lower()
            if status in {"completed", "succeeded", "success"}:
                self.ingest_atlas_callback({**payload, "task_id": binding.provider_task_id, "status": "completed"})
                completed += 1
            elif status in {"failed", "cancelled", "canceled", "error"}:
                self.ingest_atlas_callback({**payload, "task_id": binding.provider_task_id, "status": status})
                failed += 1
            else:
                with self._factory.begin() as session:
                    row = session.get(WorkflowTaskBindingModel, binding.binding_id)
                    if row is not None:
                        row.task_status = status
                pending += 1
        return ReconciliationReport(checked=checked, completed=completed, failed=failed, pending=pending)

    def claim_next_map_item(self, worker_id: str, *, run_id: UUID | None = None) -> MapItemClaim | None:
        """Lease the next durable Map/Fold item, respecting sequential Fold mode."""
        with self._factory.begin() as session:
            stmt = select(MapItemRunModel).where(MapItemRunModel.status == "pending")
            if run_id is not None:
                stmt = stmt.where(MapItemRunModel.run_id == run_id)
            item = session.scalar(stmt.order_by(MapItemRunModel.run_id, MapItemRunModel.node_instance_id, MapItemRunModel.item_index).with_for_update(skip_locked=True).limit(1))
            if item is None:
                return None
            flow = session.scalar(select(ForEachRunModel).where(ForEachRunModel.run_id == item.run_id, ForEachRunModel.node_instance_id == item.node_instance_id))
            if flow is None:
                raise ConflictError("Map item has no ForEachRun")
            if flow.mode == "sequential":
                earlier = session.scalar(select(func.count()).select_from(MapItemRunModel).where(
                    MapItemRunModel.run_id == item.run_id, MapItemRunModel.node_instance_id == item.node_instance_id,
                    MapItemRunModel.item_index < item.item_index, MapItemRunModel.status != "completed",
                )) or 0
                if earlier:
                    return None
            item.status, item.started_at = "running", datetime.now(timezone.utc)
            item_node = session.scalar(select(NodeRunModel).where(
                NodeRunModel.run_id == item.run_id,
                NodeRunModel.node_instance_id == f"{item.node_instance_id}[{item.item_index}]",
            ))
            if item_node is None:
                raise ConflictError("Map item has no expanded NodeRun")
            attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == item_node.node_run_id))
            if attempt is None or attempt.status != AttemptStatus.PENDING:
                raise ConflictError("Map item has no pending attempt")
            attempt.status, attempt.lease_id, attempt.started_at = AttemptStatus.LEASED, worker_id, datetime.now(timezone.utc)
            item_node.status = NodeRunStatus.RUNNING
            return MapItemClaim(item.map_item_id, item.run_id, item.node_instance_id, item.item_index, dict(item.item_value or {}))

    def complete_map_item(self, map_item_id: UUID, result: dict[str, Any], *, expected_epoch: int | None = None) -> None:
        """Persist item output and the Fold accumulator checkpoint atomically."""
        with self._factory.begin() as session:
            item = session.get(MapItemRunModel, map_item_id)
            if item is None or item.status not in {"running", "completed"}:
                raise ConflictError("Map item is not claimable for completion")
            flow = session.scalar(select(ForEachRunModel).where(ForEachRunModel.run_id == item.run_id, ForEachRunModel.node_instance_id == item.node_instance_id))
            if flow is None:
                raise ConflictError("Map item has no ForEachRun")
            if item.status != "completed":
                item.status, item.result, item.completed_at = "completed", result, datetime.now(timezone.utc)
                flow.completed_count += 1
                item_node = session.scalar(select(NodeRunModel).where(
                    NodeRunModel.run_id == item.run_id,
                    NodeRunModel.node_instance_id == f"{item.node_instance_id}[{item.item_index}]",
                ))
                if item_node is None:
                    raise ConflictError("Map item has no expanded NodeRun")
                attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == item_node.node_run_id))
                if attempt is None:
                    raise ConflictError("Map item has no attempt")
                if expected_epoch is not None and attempt.execution_epoch != expected_epoch:
                    raise ConflictError("Map item execution epoch is stale")
                # A mapped fixed Agent publishes its provider result first,
                # therefore its item convergence legitimately observes a
                # completed attempt. Generic map executors still complete
                # from their active lease.
                if attempt.status not in {AttemptStatus.LEASED, AttemptStatus.RUNNING, AttemptStatus.COMPLETED}:
                    raise ConflictError("Map item attempt is no longer owned by this worker")
                fixed = dict(attempt.fixed_input or {})
                run = session.get(WorkflowRunModel, item.run_id)
                if run is None:
                    raise NotFoundError("WorkflowRun", str(item.run_id))
                artifact_id, version_id = uuid.uuid4(), uuid.uuid4()
                session.add(ArtifactVersionModel(artifact_version_id=version_id, artifact_id=artifact_id,
                    schema_id="map_item_output", schema_version=1, owner_scope=run.owner_scope,
                    content_json=result, content_hash=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
                    content_uri="", blob_uri="", created_by_run_id=run.run_id,
                    lineage_input_refs=[{"map_item_id": str(item.map_item_id), "item_index": item.item_index, "fixed_input": fixed}],
                    metadata_json={"map_item_id": str(item.map_item_id), "node_instance_id": item.node_instance_id}, created_at=datetime.now(timezone.utc)))
                fixed["map_output"] = {"artifact_version_id": str(version_id)}
                attempt.fixed_input, attempt.status, attempt.completed_at = fixed, AttemptStatus.COMPLETED, datetime.now(timezone.utc)
                item_node.status = NodeRunStatus.COMPLETED
            if flow.mode == "sequential" and (flow.config or {}).get("fold", False):
                previous = session.scalar(select(FoldCheckpointModel).where(
                    FoldCheckpointModel.for_each_id == flow.for_each_id,
                ).order_by(FoldCheckpointModel.item_index.desc(), FoldCheckpointModel.execution_epoch.desc()).limit(1))
                expected_index = 0 if previous is None else previous.item_index + 1
                if item.item_index != expected_index:
                    raise ConflictError("Fold checkpoint continuity was lost")
                config = dict(flow.config or {})
                config["checkpoint"] = {"item_index": item.item_index, "accumulator": result}
                flow.config = config
                session.add(FoldCheckpointModel(checkpoint_id=uuid.uuid4(), for_each_id=flow.for_each_id,
                    item_index=item.item_index, execution_epoch=attempt.execution_epoch, accumulator=result))
            if flow.completed_count + flow.failed_count >= flow.item_count:
                flow.status = "completed" if flow.failed_count == 0 else "failed"
                parent = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == item.run_id, NodeRunModel.node_instance_id == item.node_instance_id))
                run = session.get(WorkflowRunModel, item.run_id)
                if parent is not None and run is not None and parent.status != NodeRunStatus.COMPLETED:
                    # The parent Map/Fold output is a deterministic ordered
                    # aggregate ArtifactVersion.  It is what downstream and
                    # partial runs consume, while per-item artifacts retain
                    # their individual lineage.
                    item_outputs = list(session.scalars(select(MapItemRunModel).where(
                        MapItemRunModel.run_id == item.run_id,
                        MapItemRunModel.node_instance_id == item.node_instance_id,
                    ).order_by(MapItemRunModel.item_index)))
                    ordered_refs: list[dict[str, Any]] = []
                    for output_item in item_outputs:
                        child_node = session.scalar(select(NodeRunModel).where(
                            NodeRunModel.run_id == item.run_id,
                            NodeRunModel.node_instance_id == f"{item.node_instance_id}[{output_item.item_index}]",
                        ))
                        child_attempt = session.scalar(select(NodeRunAttemptModel).where(
                            NodeRunAttemptModel.node_run_id == child_node.node_run_id if child_node is not None else False,
                        )) if child_node is not None else None
                        ref = dict(child_attempt.fixed_input or {}).get("map_output") if child_attempt is not None else None
                        if not isinstance(ref, dict) or not ref.get("artifact_version_id"):
                            raise ConflictError("Map aggregate is missing an item artifact")
                        ordered_refs.append({"item_index": output_item.item_index, **ref})
                    aggregate_id, aggregate_version_id = uuid.uuid4(), uuid.uuid4()
                    aggregate = {"items": ordered_refs, "fold_checkpoint": (flow.config or {}).get("checkpoint")}
                    session.add(ArtifactVersionModel(
                        artifact_version_id=aggregate_version_id, artifact_id=aggregate_id,
                        schema_id="map_output", schema_version=1, owner_scope=run.owner_scope,
                        content_json=aggregate, content_hash=hashlib.sha256(json.dumps(aggregate, sort_keys=True).encode()).hexdigest(),
                        content_uri="", blob_uri="", created_by_run_id=run.run_id,
                        lineage_input_refs=ordered_refs,
                        metadata_json={"for_each_id": str(flow.for_each_id), "node_instance_id": item.node_instance_id},
                        created_at=datetime.now(timezone.utc),
                    ))
                    parent_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == parent.node_run_id))
                    if parent_attempt is None:
                        parent_attempt = NodeRunAttemptModel(
                            attempt_id=uuid.uuid4(), node_run_id=parent.node_run_id, attempt_number=1,
                            execution_epoch=1, fixed_input={}, status=AttemptStatus.COMPLETED,
                            completed_at=datetime.now(timezone.utc),
                        )
                        session.add(parent_attempt)
                    parent_fixed = dict(parent_attempt.fixed_input or {})
                    parent_fixed["map_output"] = {"artifact_version_id": str(aggregate_version_id)}
                    parent_attempt.fixed_input, parent_attempt.status, parent_attempt.completed_at = parent_fixed, AttemptStatus.COMPLETED, datetime.now(timezone.utc)
                    parent.status = NodeRunStatus.COMPLETED if flow.status == "completed" else NodeRunStatus.FAILED
                    self._runtime._sql_schedule_ready(session, run)

    def fail_map_item(self, map_item_id: UUID, error: str) -> None:
        with self._factory.begin() as session:
            item = session.get(MapItemRunModel, map_item_id)
            if item is None or item.status != "running":
                raise ConflictError("Map item is not running")
            flow = session.scalar(select(ForEachRunModel).where(ForEachRunModel.run_id == item.run_id, ForEachRunModel.node_instance_id == item.node_instance_id))
            if flow is None:
                raise ConflictError("Map item has no ForEachRun")
            item.status, item.error, item.completed_at = "failed", error, datetime.now(timezone.utc)
            flow.failed_count += 1
            item_node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == item.run_id, NodeRunModel.node_instance_id == f"{item.node_instance_id}[{item.item_index}]"))
            if item_node is not None:
                item_node.status = NodeRunStatus.FAILED
                attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == item_node.node_run_id))
                if attempt is not None:
                    attempt.status, attempt.completed_at = AttemptStatus.FAILED, datetime.now(timezone.utc)
            if (flow.config or {}).get("failure_policy", "fail_fast") == "fail_fast":
                flow.status = "failed"
                for waiting in session.scalars(select(MapItemRunModel).where(MapItemRunModel.run_id == item.run_id, MapItemRunModel.node_instance_id == item.node_instance_id, MapItemRunModel.status == "pending")):
                    waiting.status, waiting.completed_at = "skipped", datetime.now(timezone.utc)

    def recover_map_items(self) -> int:
        """A crashed worker never loses a map item: running items are re-leased."""
        with self._factory.begin() as session:
            rows = list(session.scalars(select(MapItemRunModel).where(MapItemRunModel.status == "running")))
            for row in rows:
                row.status, row.started_at = "pending", None
            return len(rows)

    def recover_subworkflow_timeouts(self) -> int:
        """Expire bounded child runs and atomically fail their parent node.

        A timeout is part of the compiled SubworkflowCall config.  It is
        enforced by the restart/recovery worker, so browser or API restarts
        cannot leave an unbounded child consuming budget indefinitely.
        """
        now = datetime.utcnow()
        expired = 0
        with self._factory.begin() as session:
            bindings = list(session.scalars(select(SubworkflowModel).where(SubworkflowModel.status.in_(["pending", "running"]))))
            for binding in bindings:
                timeout_seconds = (binding.config or {}).get("timeout_seconds")
                if timeout_seconds is None:
                    timeout_seconds = (binding.config or {}).get("timeout_minutes", 0)
                    timeout_seconds = int(timeout_seconds) * 60 if timeout_seconds else 0
                try:
                    timeout_seconds = int(timeout_seconds)
                except (TypeError, ValueError):
                    timeout_seconds = 0
                if timeout_seconds <= 0 or binding.created_at + timedelta(seconds=timeout_seconds) > now:
                    continue
                child = session.get(WorkflowRunModel, binding.child_run_id) if binding.child_run_id else None
                if child is not None and child.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
                    child.status = RunStatus.CANCELLED
                binding.status = "timed_out"
                parent_node = session.scalar(select(NodeRunModel).where(
                    NodeRunModel.run_id == binding.run_id,
                    NodeRunModel.node_instance_id == binding.node_instance_id,
                ))
                parent = session.get(WorkflowRunModel, binding.run_id)
                if parent_node is not None:
                    parent_node.status = NodeRunStatus.FAILED
                if parent is not None:
                    self._runtime._sql_aggregate_run(session, parent)
                session.add(OutboxEventModel(
                    event_id=uuid.uuid4(), aggregate_type="subworkflow", aggregate_id=binding.subworkflow_id,
                    event_type="subworkflow.timed_out", purpose="runtime_recovery",
                    payload={"run_id": str(binding.run_id), "child_run_id": str(binding.child_run_id)}, created_at=now,
                ))
                expired += 1
        return expired

    def consume_tool_dispatches(self, *, limit: int = 32) -> dict[str, int]:
        """Consume durable Tool dispatch outbox events from the worker plane.

        This is intentionally outside HTTP request handling. ToolBroker marks
        each claimed event published only after it reaches a terminal result
        or ``unknown`` reconciliation state, so crashes cannot cause a blind
        replay of an already submitted external side effect.
        """
        with self._factory() as session:
            events = list(session.scalars(select(OutboxEventModel).where(
                OutboxEventModel.aggregate_type == "tool_invocation",
                OutboxEventModel.purpose == "tool_dispatch",
                OutboxEventModel.published_at.is_(None),
            ).order_by(OutboxEventModel.created_at).limit(limit)))
        broker = ToolBroker(self._factory)
        counts = {"completed": 0, "unknown": 0, "failed": 0, "cancelled": 0}
        for event in events:
            result = broker.consume_dispatch_event(event.event_id)
            counts[result] = counts.get(result, 0) + 1
        return counts

    def consume_provider_dispatch_outbox(
        self, *, limit: int = 64, adapter: AtlasCloudAdapter | None = None,
    ) -> dict[str, int]:
        """Submit committed frozen Atlas contracts, with crash-safe idempotency.

        The request contract is written alongside the provider attempt and
        dispatch outbox.  A lease is committed *before* the network call.  If
        a process dies before it can persist a response, a later worker may
        submit the exact same stable idempotency key.  Once the adapter says
        the outcome is unknown, the attempt is fenced to UNKNOWN and is never
        sent again automatically.
        """
        adapter = adapter or AtlasCloudAdapter()
        with self._factory() as session:
            event_ids = list(session.scalars(select(OutboxEventModel.event_id).where(
                OutboxEventModel.purpose == "provider_dispatch",
                OutboxEventModel.published_at.is_(None),
            ).order_by(OutboxEventModel.created_at).limit(limit)))
        published = unknown = 0
        for event_id in event_ids:
            claimed = self._claim_provider_dispatch(event_id)
            if claimed is None:
                # A previous synchronous boundary may already have persisted
                # its task binding/result.  Claiming that fact publishes the
                # audit event without another provider call.
                with self._factory() as session:
                    event = session.get(OutboxEventModel, event_id)
                    if event is not None and event.published_at is not None:
                        published += 1
                continue
            provider_id, model_id, idempotency_key, contract = claimed
            if contract is None:
                self._mark_provider_unknown(event_id, provider_id, "dispatch_contract_missing")
                unknown += 1
                continue
            try:
                result = adapter.submit(
                    operation=str(contract["operation"]), model_id=model_id,
                    payload=dict(contract["request"]), idempotency_key=idempotency_key,
                )
            except AtlasSubmissionUnknown:
                self._mark_provider_unknown(event_id, provider_id, "atlas_submission_outcome_unknown")
                unknown += 1
                continue
            except Exception:
                # A deterministic provider rejection is not an uncertain
                # charge. Make the attempt terminal and do not spin on it.
                self._fail_provider_dispatch(event_id, provider_id)
                continue
            try:
                if result.asynchronous:
                    if not result.task_id:
                        raise ValidationError_("AtlasCloud async submission lacks task id")
                    self._runtime.bind_provider_task(provider_id, result.task_id)
                    self._publish_provider_dispatch(event_id)
                    published += 1
                    continue
                if not result.outputs or not all(isinstance(item, dict) for item in result.outputs):
                    raise ValidationError_("AtlasCloud provider output must contain typed objects")
                schema = dict(contract.get("result_schema") or {})
                self._runtime.publish_provider_json_outputs(
                    provider_id, owner_scope=str(schema["owner_scope"]),
                    schema_id=str(schema["schema_id"]), schema_version=int(schema["schema_version"]),
                    outputs=result.outputs, model_version=result.model_version,
                    response_fingerprint=result.raw_fingerprint, usage=result.usage,
                    actual_cost=result.actual_cost, current_epoch=int(contract["expected_epoch"]),
                )
                if contract.get("kind") == "recipe_operator":
                    with self._factory() as session:
                        provider = session.get(ProviderInvocationAttemptModel, provider_id)
                        child_id = provider.node_run_attempt_id if provider else None
                    if child_id is not None:
                        from src.domain.recipe.recipe_runtime import RecipeRuntimeService
                        RecipeRuntimeService(self._factory).advance_completed_child(child_id)
                self._publish_provider_dispatch(event_id)
                published += 1
            except (ConflictError, ValidationError_, NotFoundError):
                # A late/superseded result must never become a fresh output.
                self._fail_provider_dispatch(event_id, provider_id)
        return {"published": published, "unknown": unknown}

    def _claim_provider_dispatch(self, event_id: UUID) -> tuple[UUID, str, str, dict[str, Any] | None] | None:
        """Acquire a durable send lease without holding a DB transaction open."""
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id, with_for_update=True)
            if event is None or event.published_at is not None:
                return None
            provider = session.get(ProviderInvocationAttemptModel, event.aggregate_id)
            if provider is None:
                event.published_at = now
                return None
            attempt = session.get(NodeRunAttemptModel, provider.node_run_attempt_id)
            if attempt is None or attempt.status in {
                AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED, AttemptStatus.COMPLETED,
            }:
                # A cancellation/retry fence committed before this worker
                # acquired the send lease.  Never create the external side
                # effect after that fence.
                event.published_at = now
                if attempt is not None and attempt.status == AttemptStatus.SUPERSEDED:
                    provider.status = AttemptStatus.SUPERSEDED
                elif attempt is not None and attempt.status == AttemptStatus.CANCELLED:
                    provider.status = AttemptStatus.CANCELLED
                return None
            binding = session.scalar(select(WorkflowTaskBindingModel).where(WorkflowTaskBindingModel.provider_attempt_id == provider.provider_attempt_id))
            record = session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id))
            if binding is not None or record is not None:
                event.published_at = now
                return None
            payload = dict(event.payload or {})
            lease_raw = payload.get("dispatch_lease_until")
            if isinstance(lease_raw, str):
                try:
                    lease_until = datetime.fromisoformat(lease_raw)
                    if lease_until.tzinfo is None:
                        lease_until = lease_until.replace(tzinfo=timezone.utc)
                    if lease_until > now:
                        return None
                except ValueError:
                    pass
            payload["dispatch_lease_until"] = (now + self._lease_duration).isoformat()
            event.payload = payload
            event.retry_count = int(event.retry_count or 0) + 1
            contract = payload.get("dispatch")
            return provider.provider_attempt_id, provider.model_id, provider.idempotency_key, dict(contract) if isinstance(contract, dict) else None

    def _ensure_provider_reconcile(self, provider_attempt_id: UUID, reason: str) -> None:
        with self._factory.begin() as session:
            provider = session.get(ProviderInvocationAttemptModel, provider_attempt_id)
            if provider is None:
                return
            existing = session.scalar(select(OutboxEventModel).where(
                OutboxEventModel.aggregate_id == provider_attempt_id,
                OutboxEventModel.purpose == "provider_reconcile",
                OutboxEventModel.published_at.is_(None),
            ))
            if existing is None:
                session.add(OutboxEventModel(
                    event_id=uuid.uuid4(), aggregate_type="provider_invocation", aggregate_id=provider_attempt_id,
                    event_type="provider.reconcile_requested", purpose="provider_reconcile",
                    payload={"provider_id": provider.provider_id, "idempotency_key": provider.idempotency_key, "reason": reason},
                    dedupe_key=str(provider_attempt_id),
                    created_at=datetime.now(timezone.utc),
                ))

    def _mark_provider_unknown(self, event_id: UUID, provider_attempt_id: UUID, reason: str) -> None:
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id, with_for_update=True)
            provider = session.get(ProviderInvocationAttemptModel, provider_attempt_id)
            if event is not None:
                event.published_at = datetime.now(timezone.utc)
            if provider is not None:
                provider.status = AttemptStatus.UNKNOWN
                attempt = session.get(NodeRunAttemptModel, provider.node_run_attempt_id)
                if attempt is not None:
                    attempt.status = AttemptStatus.UNKNOWN
        self._ensure_provider_reconcile(provider_attempt_id, reason)

    def _publish_provider_dispatch(self, event_id: UUID) -> None:
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id, with_for_update=True)
            if event is not None:
                event.published_at = datetime.now(timezone.utc)

    def _fail_provider_dispatch(self, event_id: UUID, provider_attempt_id: UUID) -> None:
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id, with_for_update=True)
            provider = session.get(ProviderInvocationAttemptModel, provider_attempt_id)
            if event is not None:
                event.published_at = datetime.now(timezone.utc)
            if provider is not None:
                if provider.status not in {AttemptStatus.COMPLETED, AttemptStatus.CANCELLED}:
                    provider.status = AttemptStatus.FAILED
                attempt = session.get(NodeRunAttemptModel, provider.node_run_attempt_id)
                if attempt is not None and attempt.status not in {AttemptStatus.SUPERSEDED, AttemptStatus.CANCELLED, AttemptStatus.COMPLETED}:
                    attempt.status = AttemptStatus.FAILED

    def deliver_outbox(
        self,
        deliver: Callable[[OutboxEvent], None],
        *,
        limit: int = 64,
        purposes: set[str] | None = None,
        event_ids: set[UUID] | None = None,
    ) -> dict[str, int]:
        """Deliver committed domain events with durable retry accounting.

        The callback deliberately runs *outside* the database transaction. A
        failed consumer therefore leaves the business write intact and the
        row pending; a subsequent worker process can retry the same event.
        ``published_at`` is set only after the callback returns successfully.
        Provider dispatch itself is excluded by callers because it has the
        stricter idempotency/reconciliation path above.
        """
        with self._factory() as session:
            query = select(OutboxEventModel.event_id).where(
                OutboxEventModel.published_at.is_(None),
            ).order_by(OutboxEventModel.created_at).limit(limit)
            if purposes is not None:
                query = query.where(OutboxEventModel.purpose.in_(purposes))
            if event_ids is not None:
                if not event_ids:
                    return {"delivered": 0, "failed": 0}
                query = query.where(OutboxEventModel.event_id.in_(event_ids))
            event_ids = list(session.scalars(query))

        delivered = failed = 0
        for event_id in event_ids:
            with self._factory() as session:
                row = session.get(OutboxEventModel, event_id)
                if row is None or row.published_at is not None:
                    continue
                event = self._runtime._outbox_schema(row)
            try:
                deliver(event)
            except Exception:
                with self._factory.begin() as session:
                    row = session.get(OutboxEventModel, event_id, with_for_update=True)
                    if row is not None and row.published_at is None:
                        row.retry_count = int(row.retry_count or 0) + 1
                failed += 1
                continue
            with self._factory.begin() as session:
                row = session.get(OutboxEventModel, event_id, with_for_update=True)
                if row is not None and row.published_at is None:
                    row.published_at = datetime.now(timezone.utc)
                    delivered += 1
        return {"delivered": delivered, "failed": failed}

    def expire_due_human_tasks(self) -> int:
        """Run the durable Human Gate deadline scanner from the worker plane."""
        return self._runtime.expire_due_human_tasks()

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover_pending(self) -> RecoveryReport:
        """Recover durable state after restart without re-submitting unknown work.

        Three passes:
          1. ``recover_stale_leases`` reclaims attempts whose lease has
             already expired; the parent NodeRun is reset to ``PENDING``
             so the scheduler materialises a fresh attempt on the next
             pass.  Provider dispatch outbox is never replayed here.
          2. ``UNKNOWN`` attempts remain UNKNOWN until provider task binding
             reconciliation or an authenticated callback proves an outcome.
          3. ``WAITING_EXTERNAL`` provider attempts whose lease has expired
             are re-emitted as ``provider.dispatch`` outbox events so the
             runner can reissue the call.

        All passes are committed atomically each; the function returns a
        snapshot of the resulting counts.
        """
        now = datetime.now(timezone.utc)
        recovered_leases = self._runtime.recover_stale_leases(now=now)
        self.recover_subworkflow_timeouts()
        # A Tool submission whose worker died after recording its durable
        # idempotency fence is unknown, never eligible for a blind replay.
        ToolBroker(self._factory).recover_expired_dispatches()
        unknown_count = 0
        requeued = 0
        with self._factory.begin() as session:
            unknown_count = session.scalar(
                select(func.count()).select_from(NodeRunAttemptModel).where(
                    NodeRunAttemptModel.status == AttemptStatus.UNKNOWN,
                )
            ) or 0

        with self._factory.begin() as session:
            stmt = select(ProviderInvocationAttemptModel).where(
                ProviderInvocationAttemptModel.status == AttemptStatus.WAITING_EXTERNAL,
            )
            waiting = list(session.scalars(stmt))
            waiting_count = len(waiting)
            for provider_attempt in waiting:
                attempt = session.get(NodeRunAttemptModel, provider_attempt.node_run_attempt_id)
                if attempt is None:
                    continue
                lease_expires_at = attempt.lease_expires_at
                if lease_expires_at is not None and lease_expires_at.tzinfo is None:
                    lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
                if lease_expires_at is not None and lease_expires_at > now:
                    continue
                # A worker may have submitted immediately before it crashed.
                # The durable idempotency key/task binding can only be queried
                # or reconciled; creating another dispatch here risks a second
                # charge for providers that did receive the first request.
                provider_attempt.status = AttemptStatus.UNKNOWN
                attempt.status = AttemptStatus.UNKNOWN
                session.add(OutboxEventModel(
                    event_id=uuid.uuid4(),
                    aggregate_type="provider_invocation",
                    aggregate_id=provider_attempt.provider_attempt_id,
                    event_type="provider.reconcile_requested",
                    payload={
                        "provider_id": provider_attempt.provider_id,
                        "idempotency_key": provider_attempt.idempotency_key,
                        "reason": "lease_expired_submission_unknown",
                    },
                    purpose="provider_reconcile",
                    created_at=now,
                ))
                requeued += 1

        return RecoveryReport(
            unknown_attempts=unknown_count,
            waiting_external=waiting_count,
            requeued_attempts=requeued + recovered_leases,
        )

    def heartbeat(self, attempt_id: UUID, worker_id: str, *, ttl: timedelta | None = None) -> datetime:
        """Refresh the lease for an attempt the worker is still executing.

        A worker should call this in a tight loop (e.g. every 30-60s) for
        any attempt whose execution may exceed ``DEFAULT_LEASE_DURATION``.
        Returns the new ``lease_expires_at`` so the worker can schedule its
        next heartbeat deterministically.
        """
        return self._runtime.heartbeat_attempt(attempt_id, worker_id=worker_id, ttl=ttl)

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _runtime_to_schema(row: WorkflowRunModel) -> WorkflowRun:
        # Mirror the helper used by RuntimeService to keep a single shape.
        return RuntimeWorker._workflow_run_schema(row)

    @staticmethod
    def _workflow_run_schema(row: WorkflowRunModel) -> WorkflowRun:
        kind, _, raw_id = row.owner_scope.partition(":")
        return WorkflowRun(
            run_id=row.run_id,
            workflow_revision_id=row.workflow_revision_id,
            compiled_plan_id=row.compiled_plan_id,
            owner_scope=_owner_scope(kind, raw_id),
            input_snapshot=row.input_snapshot or {},
            status=row.status,
            created_at=row.created_at,
        )

    @staticmethod
    def _attempt_to_schema(row: NodeRunAttemptModel) -> NodeRunAttempt:
        return NodeRunAttempt(
            attempt_id=row.attempt_id,
            node_run_id=row.node_run_id,
            attempt_number=row.attempt_number,
            execution_epoch=row.execution_epoch,
            lease_id=row.lease_id,
            lease_expires_at=row.lease_expires_at,
            fixed_input=row.fixed_input or {},
            status=row.status,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )


def _owner_scope(kind: str, raw_id: str) -> Any:
    """Build an OwnerScope from a stored ``owner_scope`` string."""
    from src.schemas.models import OwnerScope

    return OwnerScope(kind=kind or "user", id=UUID(raw_id) if raw_id else uuid.uuid4())


__all__ = ["RuntimeWorker", "AttemptClaim", "ProviderResultReceipt", "RecoveryReport"]
