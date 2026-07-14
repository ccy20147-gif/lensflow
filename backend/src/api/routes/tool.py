"""Approved tool registry and non-exportable credential binding APIs."""
from __future__ import annotations

from datetime import datetime
import hmac
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.core.config import settings
from src.domain.agent.tool_broker import ToolBroker, _fingerprint
from src.infra.db.models import ToolDefinitionModel, ToolRevisionModel
from src.infra.db.session import get_session_factory
from src.api.auth import require_owner

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])
_factory = get_session_factory()


class CreateToolRequest(BaseModel):
    name: str
    description: str = ""
    owner_scope: str
    provider_type: str = ""


class CreateRevisionRequest(BaseModel):
    body: dict[str, Any]


class BindCredentialRequest(BaseModel):
    scopes: list[str]
    secret: str
    expires_at: datetime | None = None


class InvokeToolRequest(BaseModel):
    agent_revision_id: UUID
    tool_revision_id: UUID
    operation_id: str
    requested_scopes: list[str]
    input: dict[str, Any] = {}
    disclosure_fields: list[str] = []
    usage: dict[str, Any] = {}
    node_run_attempt_id: UUID | None = None


def _admin(internal_admin_key: str | None) -> None:
    """Fail closed: request JSON can never grant an administrative role."""
    configured = settings.tool_internal_admin_key
    if not configured or not internal_admin_key or not hmac.compare_digest(configured, internal_admin_key):
        raise ForbiddenError("工具管理仅开放给已配置的内部管理员")


def _tool_definition(body: dict[str, Any]) -> None:
    if body.get("risk_level") not in {"low", "medium", "high"}:
        raise ValidationError_("ToolRevision requires risk_level low, medium, or high")
    data_classes = body.get("data_classifications")
    if not isinstance(data_classes, list) or not data_classes or any(not isinstance(item, str) or not item for item in data_classes):
        raise ValidationError_("ToolRevision requires non-empty data_classifications")
    sanitizer = body.get("sanitizer_policy")
    if not isinstance(sanitizer, dict) or not isinstance(sanitizer.get("policy_version"), str) or not sanitizer["policy_version"]:
        raise ValidationError_("ToolRevision requires a frozen sanitizer_policy.policy_version")
    egress = body.get("egress_policy")
    if not isinstance(egress, dict) or not egress.get("allowed_domains"):
        raise ValidationError_("ToolRevision requires an explicit egress allowlist")
    for key, lower, upper in (("timeout_seconds", 1, 300), ("max_request_bytes", 1, 10_000_000), ("max_response_bytes", 1, 50_000_000)):
        value = egress.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise ValidationError_(f"ToolRevision egress_policy.{key} is outside allowed bounds")
    operations = body.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValidationError_("ToolRevision requires non-empty operations")
    for operation in operations:
        if not isinstance(operation, dict) or not operation.get("id"):
            raise ValidationError_("Tool operation requires id")
        if not isinstance(operation.get("input_schema", {}), dict) or not isinstance(operation.get("output_schema", {}), dict):
            raise ValidationError_("Tool operation requires typed input/output schemas")
        limits = operation.get("execution_limits")
        if not isinstance(limits, dict):
            raise ValidationError_("Tool operation requires execution_limits")
        for key in ("max_calls_per_step", "max_calls_per_run", "max_concurrency", "max_retries"):
            value = limits.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError_(f"Tool operation execution_limits.{key} must be a non-negative integer")
        for key in ("max_cost", "cost_estimate"):
            value = limits.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValidationError_(f"Tool operation execution_limits.{key} must be a non-negative number")


@router.post("", status_code=201)
def create_tool(body: CreateToolRequest, x_internal_admin_key: str | None = Header(default=None)) -> dict[str, str]:
    try:
        _admin(x_internal_admin_key)
        with _factory.begin() as session:
            row = ToolDefinitionModel(tool_id=uuid4(), name=body.name, description=body.description,
                owner_scope=body.owner_scope, provider_type=body.provider_type, approval_status="pending")
            session.add(row)
            session.flush()
            return {"tool_id": str(row.tool_id), "approval_status": str(row.approval_status)}
    except (ForbiddenError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/{tool_id}/revisions", status_code=201)
def create_revision(tool_id: UUID, body: CreateRevisionRequest, x_internal_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        _admin(x_internal_admin_key)
        _tool_definition(body.body)
        with _factory.begin() as session:
            if session.get(ToolDefinitionModel, tool_id) is None:
                raise NotFoundError("ToolDefinition", str(tool_id))
            count = len(list(session.scalars(select(ToolRevisionModel).where(ToolRevisionModel.tool_id == tool_id))))
            row = ToolRevisionModel(revision_id=uuid4(), tool_id=tool_id, revision_number=count + 1,
                body=body.body, content_hash=_fingerprint(body.body), status="draft", approval_status="pending")
            session.add(row)
            session.flush()
            return {"revision_id": str(row.revision_id), "status": str(row.status), "approval_status": str(row.approval_status)}
    except (ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/revisions/{revision_id}/approve")
def approve_revision(revision_id: UUID, x_internal_admin_key: str | None = Header(default=None)) -> dict[str, str]:
    try:
        _admin(x_internal_admin_key)
        with _factory.begin() as session:
            row = session.get(ToolRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("ToolRevision", str(revision_id))
            row.status, row.approval_status = "active", "approved"
            return {"revision_id": str(row.revision_id), "approval_status": str(row.approval_status)}
    except (ForbiddenError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/revisions/{revision_id}/suspend")
def suspend_revision(revision_id: UUID, x_internal_admin_key: str | None = Header(default=None)) -> dict[str, str]:
    try:
        _admin(x_internal_admin_key)
        ToolBroker().suspend_revision(revision_id)
        return {"revision_id": str(revision_id), "approval_status": "suspended"}
    except (ForbiddenError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/revisions/{revision_id}/credentials", status_code=201)
def bind_credential(revision_id: UUID, body: BindCredentialRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    try:
        owner_scope = require_owner(authorization)[1].scoped_id
        row = ToolBroker().bind(owner_scope=owner_scope, tool_revision_id=revision_id,
            scopes=body.scopes, secret=body.secret, expires_at=body.expires_at)
        # Deliberately no encrypted_secret or export endpoint.
        return {"binding_id": str(row.binding_id), "owner_scope": row.owner_scope,
                "tool_revision_id": str(row.tool_revision_id), "scopes": row.scopes, "status": row.status,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None}
    except (PolicyBlockedError, ValidationError_, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/credentials/{binding_id}/revoke")
def revoke_credential(binding_id: UUID, authorization: str | None = Header(None)) -> dict[str, str]:
    try:
        ToolBroker().revoke(binding_id, owner_scope=require_owner(authorization)[1].scoped_id)
        return {"binding_id": str(binding_id), "status": "revoked"}
    except (ForbiddenError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/credentials/{binding_id}/invoke", status_code=202)
def authorize_tool_invocation(binding_id: UUID, body: InvokeToolRequest, authorization: str | None = Header(None)) -> dict[str, str]:
    try:
        if not settings.tool_execution_enabled:
            # This route is intentionally not a generic HTTP executor.  Until
            # the allowlisted egress broker is deployed, reject before opening
            # a network side effect or emitting a misleading "accepted" run.
            raise PolicyBlockedError("受控 Tool 执行 Broker 未启用")
        result = ToolBroker().authorize_and_record(binding_id=binding_id, owner_scope=require_owner(authorization)[1].scoped_id,
            tool_revision_id=body.tool_revision_id, operation_id=body.operation_id,
            requested_scopes=body.requested_scopes, tool_input=body.input,
            disclosure_fields=body.disclosure_fields, usage=body.usage, dispatch=True,
            node_run_attempt_id=body.node_run_attempt_id, agent_revision_id=body.agent_revision_id)
        assert isinstance(result, tuple)
        invocation_id, event_id = result
        return {"invocation_id": str(invocation_id), "dispatch_event_id": str(event_id), "policy_decision": "allowed"}
    except (ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post("/invocations/{invocation_id}/cancel")
def cancel_tool_invocation(invocation_id: UUID, authorization: str | None = Header(None)) -> dict[str, str]:
    try:
        ToolBroker().cancel(invocation_id, owner_scope=require_owner(authorization)[1].scoped_id)
        return {"invocation_id": str(invocation_id), "status": "cancelled"}
    except (ForbiddenError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
