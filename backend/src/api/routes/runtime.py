"""TF-WF-006: Runtime API Routes — PG-backed worker commands.

Includes the human-task lifecycle (create / list / get / resolve / reject
/ timeout) used by the Demo Human Gate UI.
"""
from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID, uuid4
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.session import get_session_factory
from src.infra.db.identity_repository import get_session_store
from src.domain.workflow.compiler import WorkflowCompiler
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.registry_repository import SqlRegistryService
from src.schemas.models import OwnerScope
from src.core.config import settings

router = APIRouter(prefix="/api/v1/runtime", tags=["runtime"])

# Composition root: the worker and runtime service share the same session_factory.
_session_factory = get_session_factory()
_runtime = RuntimeService(session_factory=_session_factory)
_worker = RuntimeWorker(session_factory=_session_factory)
_sessions = get_session_store()
_registry = SqlRegistryService()
_compiler = WorkflowCompiler()
_workflows = SqlWorkflowService(_session_factory)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StartRevisionRunRequest(BaseModel):
    """Start an immutable, already-published workflow revision."""

    workflow_revision_id: UUID
    input_snapshot: dict[str, Any] = {}


class PartialClosureRequest(BaseModel):
    selected_node_ids: list[str]
    mode: str


class ClaimRequest(BaseModel):
    worker_id: str
    run_id: UUID | None = None  # optional filter to a specific run


class ExecuteBusinessAttemptRequest(BaseModel):
    attempt_id: UUID


def _require_worker(worker_key: str | None) -> None:
    """Fail closed for all worker/provider control-plane mutations."""
    if not settings.runtime_worker_key or not worker_key or not hmac.compare_digest(worker_key, settings.runtime_worker_key):
        raise HTTPException(status_code=401, detail="Invalid worker credential")


class ProviderResultRequest(BaseModel):
    model_version: str
    response_fingerprint: str
    usage: dict[str, Any] = {}
    actual_cost: float = 0.0
    output_artifact_version_ids: list[UUID] = []
    current_epoch: int | None = None


class CreateHumanTaskRequest(BaseModel):
    run_id: UUID
    node_run_id: UUID
    attempt_id: UUID
    task_kind: str = "human_gate"
    policy_strength: str = "domain_required"
    timeout_minutes: int = 60


class ResolveHumanTaskRequest(BaseModel):
    task_version: int = Field(ge=1)
    idempotency_token: str = Field(min_length=8, max_length=255)
    payload: dict[str, Any] = {}
    policy_evidence_refs: list[str] = []


class RejectHumanTaskRequest(BaseModel):
    task_version: int = Field(ge=1)
    idempotency_token: str = Field(min_length=8, max_length=255)
    reason: str = ""
    policy_evidence_refs: list[str] = []


class TimeoutHumanTaskRequest(BaseModel):
    task_version: int = Field(ge=1)
    idempotency_token: str = Field(min_length=8, max_length=255)
    reason: str = ""


class AgentRerunRequest(BaseModel):
    node_instance_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/workflow-runs", status_code=201)
async def start_revision_run(
    body: StartRevisionRunRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Compile and run a fixed revision for its authenticated owner.

    The endpoint deliberately accepts no graph, owner, plan, or Gate policy.
    They are loaded from immutable database records before the runtime creates
    any Gate task, so Workbench/Agent/Recipe callers cannot manufacture one.
    """
    _actor_id, owner = _resolve_actor(authorization)
    from src.infra.db.models import WorkflowModel, WorkflowRevisionModel
    from src.schemas.enums import RevisionStatus

    with _session_factory() as session:
        revision = session.get(WorkflowRevisionModel, body.workflow_revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="WorkflowRevision not found")
        workflow = session.get(WorkflowModel, revision.workflow_id)
        if workflow is None or workflow.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRevision not found")
        if revision.revision_status != RevisionStatus.ACTIVE:
            raise HTTPException(status_code=409, detail="WorkflowRevision is not active")

    try:
        # A run consumes the immutable plan produced at publication.  It must
        # never silently recompile against changed policy/registry state.
        plan = _workflows.get_successful_plan(body.workflow_revision_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=409, detail="Revision has no successful immutable execution plan") from exc

    run = _runtime.create_run(
        compiled_plan=plan, owner_scope=owner, input_snapshot=body.input_snapshot,
    )
    started = _runtime.start_run(run.run_id)
    return {
        "run_id": str(started.run_id), "status": started.status.value,
        "workflow_revision_id": str(body.workflow_revision_id), "plan_id": str(plan.plan_id),
    }


@router.get("/workflow-runs/{run_id}")
async def get_workflow_run(run_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-scoped persisted run snapshot for Workbench and RequestInput."""
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import (
        NodeRunAttemptModel, NodeRunModel, WorkflowRunModel,
        ProviderInvocationAttemptModel, ProviderInvocationRecordModel,
        ProviderOutputBindingModel,
    )
    with _session_factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
        nodes: list[dict[str, Any]] = []
        for node in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run_id).order_by(NodeRunModel.node_instance_id)):
            attempts = list(session.scalars(select(NodeRunAttemptModel).where(
                NodeRunAttemptModel.node_run_id == node.node_run_id,
            ).order_by(NodeRunAttemptModel.execution_epoch)))
            attempt_details: list[dict[str, Any]] = []
            for item in attempts:
                provider = session.scalar(select(ProviderInvocationAttemptModel).where(
                    ProviderInvocationAttemptModel.node_run_attempt_id == item.attempt_id,
                ).order_by(ProviderInvocationAttemptModel.created_at.desc()).limit(1))
                record = session.scalar(select(ProviderInvocationRecordModel).where(
                    ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id,
                )) if provider is not None else None
                outputs = list(session.scalars(select(ProviderOutputBindingModel.output_artifact_version_id).where(
                    ProviderOutputBindingModel.record_id == record.record_id,
                ).order_by(ProviderOutputBindingModel.output_index))) if record is not None else []
                attempt_details.append({
                    "attempt_id": str(item.attempt_id), "execution_epoch": item.execution_epoch,
                    "status": item.status.value, "fixed_input": item.fixed_input or {},
                    "provider_attempt_id": str(provider.provider_attempt_id) if provider else None,
                    "provider_id": provider.provider_id if provider else None,
                    "model_id": provider.model_id if provider else None,
                    "output_artifact_version_ids": [str(value) for value in outputs],
                    "usage": record.usage if record else {}, "actual_cost": record.actual_cost if record else None,
                })
            nodes.append({
                "node_run_id": str(node.node_run_id), "node_instance_id": node.node_instance_id,
                "node_type_id": node.node_type_id, "status": node.status.value,
                "attempts": attempt_details,
            })
        return {"run_id": str(run.run_id), "workflow_revision_id": str(run.workflow_revision_id),
                "compiled_plan_id": str(run.compiled_plan_id), "status": run.status.value,
                "input_snapshot": run.input_snapshot or {}, "nodes": nodes}


@router.get("/workflow-runs/{run_id}/agent-trace")
async def get_agent_trace(run_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-scoped, scrubbed Agent execution/readiness trace for Workbench.

    This is intentionally a read model rather than a browser reconstruction of
    generic run JSON. It exposes immutable ArtifactVersion references, costs,
    SOP phases and RequestInput recovery state, but never prompt text or a
    typed RequestInput answer.
    """
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import (
        ArtifactVersionModel, HumanTaskModel, NodeRunAttemptModel, NodeRunModel,
        ProviderInvocationAttemptModel, ProviderInvocationRecordModel,
        ProviderOutputBindingModel, WorkflowRunModel,
    )
    with _session_factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
        traces_by_attempt: dict[str, list[dict[str, Any]]] = {}
        traces = session.scalars(select(ArtifactVersionModel).where(
            ArtifactVersionModel.created_by_run_id == run_id,
            ArtifactVersionModel.owner_scope == owner.scoped_id,
            ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
        ).order_by(ArtifactVersionModel.created_at))
        for trace in traces:
            body = dict(trace.content_json or {})
            raw_attempt_id = body.get("attempt_id")
            if not raw_attempt_id:
                continue
            traces_by_attempt.setdefault(str(raw_attempt_id), []).append({
                "artifact_version_id": str(trace.artifact_version_id),
                "phase": body.get("phase"), "sop_steps": body.get("sop_steps", []),
                "failure_owner": body.get("failure_owner"), "task_id": body.get("task_id"),
                "schema_ref": body.get("schema_ref"), "answer_hash": body.get("answer_hash"),
                "usage": body.get("usage", {}), "actual_cost": body.get("actual_cost"),
                "created_at": trace.created_at.isoformat() if trace.created_at else None,
            })
        task_by_attempt: dict[str, dict[str, Any]] = {}
        for task in session.scalars(select(HumanTaskModel).where(
            HumanTaskModel.run_id == run_id, HumanTaskModel.owner_layer == "agent",
            HumanTaskModel.task_kind == "request_input",
        )):
            policy = dict(task.timeout_policy or {})
            task_by_attempt[str(task.attempt_id)] = {
                "task_id": str(task.task_id), "status": task.status.value,
                "task_version": task.task_version, "schema_ref": task.schema_ref,
                "agent_revision_id": str(task.owner_revision_id) if task.owner_revision_id else None,
                "question": policy.get("question"), "input_schema": policy.get("input_schema", {}),
                "timeout_minutes": policy.get("duration_minutes"),
            }
        agents: list[dict[str, Any]] = []
        for node in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run_id).order_by(NodeRunModel.node_instance_id)):
            if node.node_type_id != "agent_invoke" and not node.node_type_id.startswith("agent.invoke."):
                continue
            attempts: list[dict[str, Any]] = []
            for attempt in session.scalars(select(NodeRunAttemptModel).where(
                NodeRunAttemptModel.node_run_id == node.node_run_id,
            ).order_by(NodeRunAttemptModel.execution_epoch)):
                fixed = dict(attempt.fixed_input or {})
                provider = session.scalar(select(ProviderInvocationAttemptModel).where(
                    ProviderInvocationAttemptModel.node_run_attempt_id == attempt.attempt_id,
                ).order_by(ProviderInvocationAttemptModel.created_at.desc()).limit(1))
                record = session.scalar(select(ProviderInvocationRecordModel).where(
                    ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id,
                )) if provider else None
                output_ids = list(session.scalars(select(ProviderOutputBindingModel.output_artifact_version_id).where(
                    ProviderOutputBindingModel.record_id == record.record_id,
                ).order_by(ProviderOutputBindingModel.output_index))) if record else []
                raw_request_input = fixed.get("request_input")
                request_input: dict[str, Any] = raw_request_input if isinstance(raw_request_input, dict) else {}
                attempts.append({
                    "attempt_id": str(attempt.attempt_id), "execution_epoch": attempt.execution_epoch,
                    "status": attempt.status.value, "agent_revision_id": fixed.get("agent_revision_id") or task_by_attempt.get(str(attempt.attempt_id), {}).get("agent_revision_id"),
                    "input_artifact_refs": fixed.get("upstream_artifact_refs", []),
                    "resource_refs": fixed.get("committed_resource_refs", []),
                    "request_input_answered": bool(request_input.get("task_id") and "answer" in request_input),
                    "output_artifact_version_ids": [str(value) for value in output_ids],
                    "provider_id": provider.provider_id if provider else None,
                    "model_id": provider.model_id if provider else None,
                    "usage": record.usage if record else {}, "actual_cost": record.actual_cost if record else None,
                    "sop_trace": traces_by_attempt.get(str(attempt.attempt_id), []),
                    "request_input": task_by_attempt.get(str(attempt.attempt_id)),
                })
            agents.append({"node_instance_id": node.node_instance_id, "node_status": node.status.value, "attempts": attempts})
        return {"run_id": str(run_id), "agents": agents}


@router.post("/workflow-runs/{run_id}/cancel")
async def cancel_workflow_run(run_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-only cancellation request for a durable workflow run."""
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import WorkflowRunModel
    with _session_factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
    try:
        cancelled = _runtime.cancel_run(run_id)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    return {"run_id": str(cancelled.run_id), "status": cancelled.status.value}


@router.post("/workflow-runs/{run_id}/closure")
async def preview_partial_closure(run_id: UUID, body: PartialClosureRequest, authorization: str | None = Header(None)) -> dict[str, list[str]]:
    """Authenticated, side-effect-free preview of a deterministic closure."""
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import WorkflowRevisionModel, WorkflowRunModel
    with _session_factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
        revision = session.get(WorkflowRevisionModel, run.workflow_revision_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="WorkflowRevision not found")
        graph = revision.graph if isinstance(revision.graph, dict) else {}
    try:
        return _compiler.partial_closure(graph=graph, selected_node_ids=body.selected_node_ids, mode=body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/workflow-runs/{run_id}/closure/execute", status_code=201)
async def execute_partial_closure(run_id: UUID, body: PartialClosureRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Start a new durable run slice from fixed outputs of an earlier run."""
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import WorkflowRunModel
    with _session_factory() as session:
        source = session.get(WorkflowRunModel, run_id)
        if source is None or source.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
    try:
        plan = _workflows.get_successful_plan(source.workflow_revision_id)
        closure = _compiler.partial_closure(
            graph=plan.resolved_graph, selected_node_ids=body.selected_node_ids, mode=body.mode,
        )
        slice_run = _runtime.create_partial_run(
            source_run_id=run_id, compiled_plan=plan, owner_scope=owner, closure=closure,
        )
        started = _runtime.start_run(slice_run.run_id)
    except (ConflictError, ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {
        "run_id": str(started.run_id), "source_run_id": str(run_id),
        "status": started.status.value, "closure": closure,
    }


@router.post("/workflow-runs/{run_id}/agents/rerun", status_code=201)
async def rerun_agent_node(run_id: UUID, body: AgentRerunRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-only single Agent rerun from immutable source-run inputs."""
    _actor, owner = _resolve_actor(authorization)
    from src.infra.db.models import WorkflowRunModel
    with _session_factory() as session:
        source = session.get(WorkflowRunModel, run_id)
        if source is None or source.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
    plan = _workflows.get_successful_plan(source.workflow_revision_id)
    node = next((item for item in plan.resolved_graph.get("nodes", []) if isinstance(item, dict) and str(item.get("id")) == body.node_instance_id), None)
    if node is None or not str(node.get("type", "")).startswith("agent.invoke."):
        raise HTTPException(status_code=422, detail="Only fixed Agent nodes may be rerun")
    closure = _compiler.partial_closure(graph=plan.resolved_graph, selected_node_ids=[body.node_instance_id], mode="selected")
    try:
        rerun = _runtime.create_partial_run(source_run_id=run_id, compiled_plan=plan, owner_scope=owner, closure=closure)
        started = _runtime.start_run(rerun.run_id)
    except (ConflictError, ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    stale = sorted(closure["skip"])
    from src.infra.db.models import OutboxEventModel
    with _session_factory.begin() as session:
        session.add(OutboxEventModel(
            event_id=uuid4(), aggregate_type="workflow_run", aggregate_id=started.run_id,
            event_type="agent.rerun.created", purpose="agent_rerun",
            payload={"source_run_id": str(run_id), "rerun_node_id": body.node_instance_id, "stale_downstream_node_ids": stale},
            created_at=datetime.now(timezone.utc),
        ))
    return {"run_id": str(started.run_id), "source_run_id": str(run_id), "rerun_node_id": body.node_instance_id, "fixed_input_snapshot": started.input_snapshot, "stale_downstream_node_ids": stale}


@router.post("/claim")
async def claim_next_attempt(body: ClaimRequest, x_worker_key: str | None = Header(None)) -> dict[str, Any]:
    """Worker claims the next READY/PENDING attempt with a lease."""
    _require_worker(x_worker_key)
    claim = _worker.claim_next_attempt(body.worker_id, run_id=body.run_id)
    if claim is None:
        return {"status": "no_pending_attempt"}
    return {
        "attempt_id": str(claim.attempt.attempt_id),
        "node_run_id": str(claim.node_run_id),
        "lease_expires_at": claim.lease_expires_at.isoformat(),
    }


@router.post("/business-attempts/execute")
async def execute_business_attempt(
    body: ExecuteBusinessAttemptRequest, x_worker_key: str | None = Header(None),
) -> dict[str, Any]:
    """Internal worker command for public business-node execution."""
    _require_worker(x_worker_key)
    try:
        outputs = _worker.execute_business_attempt(body.attempt_id)
    except (ConflictError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {"status": "waiting_user" if not outputs else "completed", "artifact_version_ids": [str(value) for value in outputs]}


@router.post("/attempts/execute")
async def execute_attempt(
    body: ExecuteBusinessAttemptRequest, x_worker_key: str | None = Header(None),
) -> dict[str, Any]:
    """Internal worker executor for a leased Agent or business node attempt."""
    _require_worker(x_worker_key)
    try:
        return _worker.execute_attempt(body.attempt_id)
    except (ConflictError, NotFoundError, PolicyBlockedError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.post("/provider-attempts/{provider_attempt_id}/result")
async def record_provider_result(
    provider_attempt_id: UUID, body: ProviderResultRequest, x_worker_key: str | None = Header(None)
) -> dict[str, Any]:
    """Record a provider invocation result; emits a result_publish outbox event."""
    _require_worker(x_worker_key)
    receipt = _worker.record_provider_result(
        provider_attempt_id=provider_attempt_id,
        model_version=body.model_version,
        response_fingerprint=body.response_fingerprint,
        usage=body.usage,
        actual_cost=body.actual_cost,
        output_artifact_version_ids=body.output_artifact_version_ids,
        current_epoch=body.current_epoch,
    )
    return {
        "record_id": str(receipt.record.record_id),
        "outbox_event_id": str(receipt.outbox.event_id),
        "status": "completed",
    }


@router.post("/recover")
async def recover(x_worker_key: str | None = Header(None)) -> dict[str, Any]:
    """Run restart-time recovery over the database."""
    _require_worker(x_worker_key)
    report = _worker.recover_pending()
    return {
        "unknown_attempts": report.unknown_attempts,
        "waiting_external": report.waiting_external,
        "requeued_attempts": report.requeued_attempts,
    }


@router.post("/reconcile/atlascloud")
async def reconcile_atlascloud(x_worker_key: str | None = Header(None)) -> dict[str, Any]:
    """Internal worker command; unknown submissions are queried, never resent."""
    _require_worker(x_worker_key)
    try:
        report = _worker.reconcile_unknown()
    except Exception as exc:
        # Provider failures must leave attempts UNKNOWN for a later safe retry.
        raise HTTPException(status_code=502, detail=f"AtlasCloud reconciliation failed: {exc}") from exc
    return report.__dict__


@router.post("/callbacks/atlascloud")
async def atlascloud_callback(request: Request, x_atlas_signature: str | None = Header(None)) -> dict[str, Any]:
    """Signed provider callback.  It can only address a pre-bound task id."""
    raw = await request.body()
    from src.domain.provider.atlascloud import AtlasCloudAdapter
    if not AtlasCloudAdapter.verify_webhook(body=raw, signature=x_atlas_signature or "", secret=settings.atlascloud_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid AtlasCloud callback signature")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid AtlasCloud callback JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="AtlasCloud callback must be an object")
    try:
        receipt = _worker.ingest_atlas_callback(payload)
    except (ConflictError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return {"accepted": True, "status": "pending" if receipt is None else "completed", "record_id": str(receipt.record.record_id) if receipt else None}


# ---------------------------------------------------------------------------
# Human task read/decision endpoints
# ---------------------------------------------------------------------------


def _human_task_to_dict(t: Any) -> dict[str, Any]:
    return {
        "task_id": str(t.task_id),
        "task_kind": t.task_kind,
        "run_id": str(t.run_id),
        "node_run_id": str(t.node_run_id),
        "attempt_id": str(t.attempt_id),
        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
        "policy_strength": getattr(t, "policy_strength", ""),
        "schema_ref": getattr(t, "schema_ref", ""),
        "input_snapshot_refs": list(getattr(t, "input_snapshot_refs", []) or []),
        "timeout_policy": getattr(t, "timeout_policy", {}) or {},
        "task_version": getattr(t, "task_version", 1),
        "created_at": (
            t.created_at.isoformat()
            if hasattr(t.created_at, "isoformat")
            else str(getattr(t, "created_at", ""))
        ),
    }


def _resolve_actor(authorization: str | None) -> tuple[UUID, OwnerScope]:
    """Derive the sole public decision actor from a verified bearer token."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        account_id = _sessions.account_for_token(parts[1])
    except (NotFoundError, ConflictError) as exc:
        # Session validation may surface a safe domain error.  Do not accept a
        # request merely because a caller supplied an arbitrary scope header.
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return account_id, OwnerScope(kind="user", id=account_id)


@router.get("/human-tasks")
async def list_human_tasks(run_id: UUID | None = None, authorization: str | None = Header(None)) -> dict[str, Any]:
    """List human tasks, optionally filtered by run_id."""
    if _session_factory is None:
        return {"tasks": []}
    actor_id, owner = _resolve_actor(authorization)
    from src.infra.db.models import HumanTaskModel, WorkflowRunModel
    from sqlalchemy import select
    with _session_factory() as session:
        stmt = select(HumanTaskModel).join(WorkflowRunModel, HumanTaskModel.run_id == WorkflowRunModel.run_id).where(
            WorkflowRunModel.owner_scope == owner.scoped_id,
        )
        if run_id is not None:
            stmt = stmt.where(HumanTaskModel.run_id == run_id)
        stmt = stmt.order_by(HumanTaskModel.created_at.desc())
        rows = session.scalars(stmt).all()
    # Map rows to schema using the runtime's helper
    return {"tasks": [_human_task_to_dict(_runtime._human_task_schema(r)) for r in rows]}


@router.get("/human-tasks/{task_id}")
async def get_human_task(task_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    if _session_factory is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    actor_id, owner = _resolve_actor(authorization)
    from src.infra.db.models import HumanTaskModel, WorkflowRunModel
    with _session_factory() as session:
        row = session.get(HumanTaskModel, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="HumanTask not found")
        run = session.get(WorkflowRunModel, row.run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="HumanTask not found")
    return _human_task_to_dict(_runtime._human_task_schema(row))


@router.post("/human-tasks/{task_id}/resolve")
async def resolve_human_task(task_id: UUID, body: ResolveHumanTaskRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """User resolved the gate → run proceeds. Idempotent: returns the same state on retry."""
    try:
        actor_id, owner = _resolve_actor(authorization)
        task = _runtime.resolve_human_task(
            task_id, payload=body.payload or {}, actor_id=actor_id, actor_scope=owner.scoped_id,
            task_version=body.task_version, idempotency_key=body.idempotency_token,
            policy_evidence_refs=body.policy_evidence_refs, internal=False,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _human_task_to_dict(task)


@router.post("/human-tasks/{task_id}/reject")
async def reject_human_task(task_id: UUID, body: RejectHumanTaskRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """User rejected the gate → run fails. Idempotent."""
    try:
        actor_id, owner = _resolve_actor(authorization)
        task = _runtime.reject_human_task(
            task_id, reason=body.reason or "", actor_id=actor_id, actor_scope=owner.scoped_id,
            task_version=body.task_version, idempotency_key=body.idempotency_token,
            policy_evidence_refs=body.policy_evidence_refs, internal=False,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _human_task_to_dict(task)


@router.post("/human-tasks/{task_id}/timeout")
async def timeout_human_task(task_id: UUID, body: TimeoutHumanTaskRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Mark the gate as expired. Idempotent."""
    try:
        actor_id, owner = _resolve_actor(authorization)
        task = _runtime.timeout_human_task(
            task_id, reason=body.reason or "", actor_id=actor_id, actor_scope=owner.scoped_id,
            task_version=body.task_version, idempotency_key=body.idempotency_token, internal=False,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
    return _human_task_to_dict(task)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def runtime_health() -> dict[str, Any]:
    """Confirm the runtime can talk to PostgreSQL."""
    try:
        with _session_factory() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "PG_UNREACHABLE", "message": str(exc)}},
        )
