"""Fail-closed Tool/Credential broker for TF-AGT-005."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import settings
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, PolicyBlockedError, ValidationError_
from src.infra.db.models import AgentDefinitionModel, AgentRevisionModel, ArtifactVersionModel, CredentialBindingModel, NodeRunAttemptModel, NodeRunModel, OutboxEventModel, ToolInvocationModel, ToolRevisionModel, WorkflowRunModel
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus
from src.domain.runtime.runtime_service import RuntimeService


_TOOL_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|bearer\s+[A-Za-z0-9._-]{12,}|(?:api[_-]?key|password|secret|token)\s*[:=])", re.I)
_TOOL_INJECTION = re.compile(r"(?:ignore\s+(?:all\s+)?(?:previous|system)\s+instructions?|reveal\s+(?:the\s+)?(?:system\s+)?prompt|disable\s+(?:all\s+)?safety)", re.I)
_TOOL_EXECUTABLE = re.compile(r"(?:<script\b|javascript:|\b(?:curl|wget|powershell|bash)\b)", re.I)


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _sanitize_tool_output(value: Any, policy: Any) -> tuple[Any, str]:
    """Fail closed before an external Tool result enters the workflow graph."""
    if not isinstance(policy, dict) or not isinstance(policy.get("policy_version"), str) or not policy["policy_version"]:
        raise PolicyBlockedError("Tool sanitizer policy is unavailable")
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if _TOOL_SECRET.search(serialized):
        raise PolicyBlockedError("Tool output secret sanitizer blocked the result")
    if _TOOL_INJECTION.search(serialized):
        raise PolicyBlockedError("Tool output prompt-injection sanitizer blocked the result")
    if _TOOL_EXECUTABLE.search(serialized):
        raise PolicyBlockedError("Tool output executable-content sanitizer blocked the result")
    return value, str(policy["policy_version"])


class ToolBroker:
    """Only this service can decrypt a binding, and it never returns it."""

    def __init__(self, factory: sessionmaker[Session] | None = None, *, encryption_key: str | None = None) -> None:
        self._factory = factory or get_session_factory()
        raw_key = encryption_key if encryption_key is not None else settings.credential_encryption_key
        if not raw_key:
            self._fernet: Fernet | None = None
        else:
            try:
                self._fernet = Fernet(raw_key.encode())
            except (ValueError, TypeError) as exc:
                raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must be a Fernet key") from exc

    def bind(self, *, owner_scope: str, tool_revision_id: UUID, scopes: list[str], secret: str,
             expires_at: datetime | None = None) -> CredentialBindingModel:
        if not secret:
            raise ValidationError_("Credential secret is required")
        if self._fernet is None:
            raise PolicyBlockedError("凭证加密密钥未配置")
        with self._factory.begin() as session:
            revision = session.get(ToolRevisionModel, tool_revision_id)
            if revision is None:
                raise NotFoundError("ToolRevision", str(tool_revision_id))
            if revision.status != "active" or revision.approval_status != "approved":
                raise PolicyBlockedError("工具修订未经批准或已暂停")
            row = CredentialBindingModel(binding_id=uuid4(), owner_scope=owner_scope,
                tool_revision_id=tool_revision_id, scopes=sorted(set(scopes)),
                encrypted_secret=self._fernet.encrypt(secret.encode()).decode(), expires_at=expires_at)
            session.add(row)
            session.flush()
            return row

    def revoke(self, binding_id: UUID, *, owner_scope: str) -> None:
        with self._factory.begin() as session:
            binding = session.get(CredentialBindingModel, binding_id, with_for_update=True)
            if binding is None:
                raise NotFoundError("CredentialBinding", str(binding_id))
            if binding.owner_scope != owner_scope:
                raise ForbiddenError("跨 owner 凭证访问被拒绝")
            binding.status = "revoked"
            binding.revoked_at = datetime.now(timezone.utc)
            self._cancel_pending(session, binding_id=binding_id)

    def suspend_revision(self, revision_id: UUID) -> None:
        """Suspend a ToolRevision and quarantine every unfinished invocation.

        The route protects this admin operation.  Keeping the state transition
        and the drain in one SQL transaction prevents a queued invocation from
        surviving an emergency suspension.
        """
        with self._factory.begin() as session:
            revision = session.get(ToolRevisionModel, revision_id, with_for_update=True)
            if revision is None:
                raise NotFoundError("ToolRevision", str(revision_id))
            revision.approval_status = "suspended"
            self._cancel_pending(session, tool_revision_id=revision_id)

    def _cancel_pending(
        self,
        session: Session,
        *,
        binding_id: UUID | None = None,
        tool_revision_id: UUID | None = None,
    ) -> None:
        """Quarantine unfinished work after entitlement loss, transactionally."""
        query = session.query(ToolInvocationModel).filter(
            ToolInvocationModel.status.in_(["authorized", "dispatched", "submitting", "unknown"]),
        )
        if binding_id is not None:
            query = query.filter(ToolInvocationModel.credential_binding_id == binding_id)
        if tool_revision_id is not None:
            query = query.filter(ToolInvocationModel.tool_revision_id == tool_revision_id)
        for row in query.with_for_update():
            row.status = "cancelled"
            row.cancellation_requested_at = datetime.now(timezone.utc)
            # The invocation might already have crossed the network boundary.
            # Any subsequently observed result is therefore quarantined.
            row.late_result_quarantined = True
            existing = session.query(OutboxEventModel).filter(
                OutboxEventModel.aggregate_type == "tool_invocation",
                OutboxEventModel.aggregate_id == row.invocation_id,
                OutboxEventModel.purpose == "tool_cancel",
            ).first()
            if existing is None:
                session.add(OutboxEventModel(
                    event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=row.invocation_id,
                    event_type="tool.cancel", purpose="tool_cancel",
                    payload={"reason": "entitlement_revoked"}, created_at=datetime.now(timezone.utc),
                ))
            self._aggregate_parent_attempt(session, row.node_run_attempt_id)

    @staticmethod
    def _execution_limits(operation: dict[str, Any]) -> dict[str, Any]:
        limits = operation.get("execution_limits")
        if not isinstance(limits, dict):
            raise PolicyBlockedError("Tool operation lacks a frozen execution limit contract")
        required = ("max_calls_per_step", "max_calls_per_run", "max_concurrency", "max_cost", "max_retries", "cost_estimate")
        if any(key not in limits for key in required):
            raise PolicyBlockedError("Tool operation has an incomplete execution limit contract")
        return limits

    def _reserve_execution_limits(
        self,
        session: Session,
        *,
        attempt_id: UUID | None,
        run_id: UUID | None,
        tool_revision_id: UUID,
        operation_id: str,
        limits: dict[str, Any],
    ) -> float:
        """Reserve a bounded invocation while the parent attempt/run is locked."""
        estimate = float(limits["cost_estimate"])
        if attempt_id is None or run_id is None:
            # Management dry-runs have no executable workflow side effect.
            return estimate
        step_count = session.query(ToolInvocationModel).filter(
            ToolInvocationModel.node_run_attempt_id == attempt_id,
            ToolInvocationModel.tool_revision_id == tool_revision_id,
            ToolInvocationModel.operation_id == operation_id,
        ).count()
        if step_count >= int(limits["max_calls_per_step"]):
            raise PolicyBlockedError("Tool step invocation limit exceeded")
        run_rows = session.query(ToolInvocationModel).join(
            NodeRunAttemptModel, ToolInvocationModel.node_run_attempt_id == NodeRunAttemptModel.attempt_id,
        ).join(
            NodeRunModel, NodeRunAttemptModel.node_run_id == NodeRunModel.node_run_id,
        ).filter(
            NodeRunModel.run_id == run_id,
            ToolInvocationModel.tool_revision_id == tool_revision_id,
            ToolInvocationModel.operation_id == operation_id,
        )
        run_values = list(run_rows.with_entities(ToolInvocationModel.reserved_cost, ToolInvocationModel.status))
        if len(run_values) >= int(limits["max_calls_per_run"]):
            raise PolicyBlockedError("Tool run invocation limit exceeded")
        active = sum(1 for _cost, status in run_values if str(status) in {"authorized", "dispatched", "submitting", "unknown"})
        if active >= int(limits["max_concurrency"]):
            raise PolicyBlockedError("Tool run concurrency limit exceeded")
        reserved = sum(float(cost or 0.0) for cost, _status in run_values)
        if reserved + estimate > float(limits["max_cost"]):
            raise PolicyBlockedError("Tool run cost limit exceeded")
        return estimate

    def authorize_and_record(self, *, binding_id: UUID, owner_scope: str, tool_revision_id: UUID,
                             operation_id: str, requested_scopes: list[str], tool_input: dict[str, Any],
                             disclosure_fields: list[str], usage: dict[str, Any] | None = None,
                             dispatch: bool = False, node_run_attempt_id: UUID | None = None,
                             agent_revision_id: UUID | None = None) -> UUID | tuple[UUID, UUID]:
        """Check current entitlement before any external request and write a safe trace."""
        with self._factory.begin() as session:
            binding = session.get(CredentialBindingModel, binding_id)
            revision = session.get(ToolRevisionModel, tool_revision_id)
            if binding is None or revision is None:
                raise NotFoundError("Tool credential or revision", str(binding_id))
            if binding.owner_scope != owner_scope or binding.tool_revision_id != tool_revision_id:
                raise ForbiddenError("凭证不属于当前 owner 或工具修订")
            run_id: UUID | None = None
            if node_run_attempt_id is not None:
                attempt = session.get(NodeRunAttemptModel, node_run_attempt_id, with_for_update=True)
                node = session.get(NodeRunModel, attempt.node_run_id, with_for_update=True) if attempt else None
                run = session.get(WorkflowRunModel, node.run_id, with_for_update=True) if node else None
                if run is None or run.owner_scope != owner_scope:
                    raise ForbiddenError("ToolInvocation attempt does not belong to current workflow owner")
                run_id = run.run_id
                if agent_revision_id is None:
                    raise PolicyBlockedError("Workflow ToolInvocation requires a frozen AgentRevision entitlement")
            now = datetime.now(timezone.utc)
            expires_at = binding.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if binding.status != "active" or (expires_at is not None and expires_at <= now):
                raise PolicyBlockedError("凭证已撤销、禁用或过期")
            if revision.status != "active" or revision.approval_status != "approved":
                raise PolicyBlockedError("工具修订不可用")
            definition = revision.body or {}
            operations = {str(item.get("id")): item for item in definition.get("operations", []) if isinstance(item, dict)}
            operation = operations.get(operation_id)
            if operation is None:
                raise PolicyBlockedError("工具操作未注册")
            entitlement_refs: list[str] = []
            agent_allowed_scopes: set[str] | None = None
            agent_allowed_fields: set[str] | None = None
            if agent_revision_id is not None:
                agent_revision = session.get(AgentRevisionModel, agent_revision_id)
                agent = session.get(AgentDefinitionModel, agent_revision.agent_id) if agent_revision else None
                if agent_revision is None or agent is None or agent.owner_scope != owner_scope or agent_revision.status != "active":
                    raise ForbiddenError("ToolInvocation AgentRevision is unavailable for this owner")
                plan_entry = next((item for item in (agent_revision.body or {}).get("tool_access_plan", [])
                                   if isinstance(item, dict) and str(item.get("tool_revision_id")) == str(tool_revision_id)), None)
                if plan_entry is None:
                    raise PolicyBlockedError("AgentRevision did not approve this ToolRevision")
                plan_operation = next((item for item in plan_entry.get("operations", [])
                                       if isinstance(item, dict) and item.get("operation_id") == operation_id), None)
                if plan_operation is None:
                    raise PolicyBlockedError("AgentRevision did not approve this Tool operation")
                agent_allowed_scopes = set(plan_operation.get("allowed_scopes", []))
                agent_allowed_fields = set(plan_operation.get("disclosure_fields", []))
                entitlement_refs.append(f"agent_revision:{agent_revision_id}")
            if not set(requested_scopes).issubset(set(binding.scopes or [])):
                raise PolicyBlockedError("请求 scope 超出凭证授权")
            if agent_allowed_scopes is not None and not set(requested_scopes).issubset(agent_allowed_scopes):
                raise PolicyBlockedError("请求 scope 超出冻结 AgentRevision 授权")
            allowed_fields = set(operation.get("disclosure_fields", []))
            if not set(disclosure_fields).issubset(allowed_fields):
                raise PolicyBlockedError("请求披露了未批准字段")
            if agent_allowed_fields is not None and not set(disclosure_fields).issubset(agent_allowed_fields):
                raise PolicyBlockedError("请求披露超出冻结 AgentRevision 授权")
            actual_fields = set(tool_input)
            if not actual_fields.issubset(set(disclosure_fields)):
                raise PolicyBlockedError("Tool 输入包含未声明的披露字段")
            if not actual_fields.issubset(allowed_fields):
                raise PolicyBlockedError("Tool 输入包含 ToolRevision 未批准字段")
            if agent_allowed_fields is not None and not actual_fields.issubset(agent_allowed_fields):
                raise PolicyBlockedError("Tool 输入包含 AgentRevision 未批准字段")
            from src.domain.agent.schema_validation import validate_json_schema
            input_schema = operation.get("input_schema") or {"type": "object"}
            validate_json_schema(tool_input, input_schema)
            disclosure_manifest = sorted(actual_fields)
            entitlement = {
                "binding_id": str(binding_id), "tool_revision_id": str(tool_revision_id),
                "agent_revision_id": str(agent_revision_id) if agent_revision_id else None,
                "operation_id": operation_id, "scopes": sorted(set(requested_scopes)),
                "disclosure_manifest_hash": _fingerprint(disclosure_manifest),
            }
            entitlement_refs.append(f"entitlement:{_fingerprint(entitlement)}")
            # Do not persist the input itself: only hashes and field names are auditable.
            idempotency_key = _fingerprint({
                "owner_scope": owner_scope, "attempt_id": str(node_run_attempt_id) if node_run_attempt_id else None,
                "tool_revision_id": str(tool_revision_id), "operation_id": operation_id,
                "input_fingerprint": _fingerprint(tool_input), "scopes": sorted(set(requested_scopes)),
                "disclosure_manifest": disclosure_manifest,
            })
            # A database transaction lock serializes the find-or-create path
            # before the partial unique index provides the durable backstop.
            # Tool execution is PostgreSQL-only in production.
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": idempotency_key})
            prior = session.query(ToolInvocationModel).filter(
                ToolInvocationModel.owner_scope == owner_scope,
                ToolInvocationModel.idempotency_key == idempotency_key,
            ).first()
            if prior is not None and prior.status in {"authorized", "dispatched", "unknown", "completed"}:
                if dispatch:
                    event = session.query(OutboxEventModel).filter(
                        OutboxEventModel.aggregate_type == "tool_invocation", OutboxEventModel.aggregate_id == prior.invocation_id,
                        OutboxEventModel.purpose == "tool_dispatch",
                    ).first()
                    if event is None:
                        if prior.status != "authorized":
                            raise ConflictError("ToolInvocation has no durable dispatch evidence")
                        event = OutboxEventModel(event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=prior.invocation_id,
                            event_type="tool.dispatch", purpose="tool_dispatch", payload={"operation_id": prior.operation_id, "input_fingerprint": prior.input_fingerprint}, created_at=datetime.now(timezone.utc))
                        prior.status = "dispatched"
                        session.add(event)
                    return prior.invocation_id, event.event_id
                return prior.invocation_id
            reserved_cost = self._reserve_execution_limits(
                session, attempt_id=node_run_attempt_id, run_id=run_id,
                tool_revision_id=tool_revision_id, operation_id=operation_id,
                limits=self._execution_limits(operation),
            )
            if self._fernet is None:
                raise PolicyBlockedError("受控 Tool 调度需要凭证加密密钥")
            sealed_input = self._fernet.encrypt(json.dumps(tool_input, sort_keys=True, separators=(",", ":")).encode()).decode()
            row = ToolInvocationModel(invocation_id=uuid4(), tool_revision_id=tool_revision_id,
                credential_binding_id=binding_id, owner_scope=owner_scope, operation_id=operation_id,
                node_run_attempt_id=node_run_attempt_id,
                input_fingerprint=_fingerprint(tool_input), disclosure_manifest=disclosure_manifest,
                disclosure_manifest_hash=_fingerprint(disclosure_manifest), policy_decision="allowed",
                decision_refs=entitlement_refs, usage={"declared": usage or {}, "_sealed_input": sealed_input},
                idempotency_key=idempotency_key, reserved_cost=reserved_cost, retry_count=0, status="authorized")
            session.add(row)
            if dispatch:
                event = OutboxEventModel(event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=row.invocation_id,
                    event_type="tool.dispatch", purpose="tool_dispatch", payload={"operation_id": row.operation_id, "input_fingerprint": row.input_fingerprint}, created_at=datetime.now(timezone.utc))
                row.status = "dispatched"
                session.add(event)
                session.flush()
                return row.invocation_id, event.event_id
            session.flush()
            return row.invocation_id

    def dispatch(self, invocation_id: UUID, *, owner_scope: str) -> UUID:
        """Atomically move an authorized invocation to dispatchable state.

        The outbox deliberately contains no credential or plain input; the
        worker resolves both only after it has claimed this durable event.
        """
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None or row.owner_scope != owner_scope:
                raise ForbiddenError("Tool invocation does not belong to current owner")
            existing = session.query(OutboxEventModel).filter(
                OutboxEventModel.aggregate_type == "tool_invocation",
                OutboxEventModel.aggregate_id == invocation_id,
                OutboxEventModel.purpose == "tool_dispatch",
            ).first()
            if existing is not None:
                return existing.event_id
            if row.status != "authorized":
                raise ConflictError("Only an authorized ToolInvocation can be dispatched")
            event = OutboxEventModel(event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                event_type="tool.dispatch", purpose="tool_dispatch", payload={"operation_id": row.operation_id, "input_fingerprint": row.input_fingerprint}, created_at=datetime.now(timezone.utc))
            row.status = "dispatched"
            session.add(event)
            session.flush()
            return event.event_id

    def execute_dispatched(self, invocation_id: UUID, *, transport: httpx.Client | None = None) -> str:
        """Worker-only bounded egress; never invoked from a browser request."""
        with self._factory.begin() as session:
            # Acquire entitlement rows before the invocation row.  Revocation
            # and suspension use the same order (binding/revision -> rows),
            # avoiding a revoke-vs-worker deadlock.
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None:
                raise NotFoundError("ToolInvocation", str(invocation_id))
            binding = session.get(CredentialBindingModel, row.credential_binding_id, with_for_update=True)
            revision = session.get(ToolRevisionModel, row.tool_revision_id, with_for_update=True)
            row = session.get(ToolInvocationModel, invocation_id, with_for_update=True)
            assert row is not None
            if row.status == "cancelled":
                return "cancelled"
            if row.status not in {"dispatched", "submitting"}:
                raise ConflictError("ToolInvocation is not dispatchable")
            now = datetime.now(timezone.utc)
            expires_at = binding.expires_at if binding is not None else None
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if (
                binding is None
                or binding.status != "active"
                or (expires_at is not None and expires_at <= now)
                or revision is None
                or revision.status != "active"
                or revision.approval_status != "approved"
            ):
                row.status = "cancelled"
                row.cancellation_requested_at = now
                row.late_result_quarantined = True
                session.add(OutboxEventModel(
                    event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                    event_type="tool.cancel", purpose="tool_cancel",
                    payload={"reason": "entitlement_recheck_failed"}, created_at=now,
                ))
                self._aggregate_parent_attempt(session, row.node_run_attempt_id)
                return "cancelled"
            if revision is None:
                raise NotFoundError("ToolRevision", str(row.tool_revision_id))
            operation = next((item for item in (revision.body or {}).get("operations", []) if isinstance(item, dict) and item.get("id") == row.operation_id), None)
            if operation is None:
                raise PolicyBlockedError("Tool operation no longer has its frozen definition")
            sealed_input = str((row.usage or {}).get("_sealed_input", ""))
            policy = dict((revision.body or {}).get("egress_policy", {}))
            limits = self._execution_limits(operation)
            if int(row.retry_count or 0) > int(limits["max_retries"]):
                raise PolicyBlockedError("Tool retry limit exceeded")
        if not sealed_input or self._fernet is None:
            raise PolicyBlockedError("Tool invocation input cannot be safely recovered")
        try:
            payload = self._fernet.decrypt(sealed_input.encode())
        except InvalidToken as exc:
            raise PolicyBlockedError("Tool invocation input ciphertext is invalid") from exc
        endpoint = str(operation.get("endpoint", ""))
        if not endpoint:
            raise PolicyBlockedError("Tool operation requires an approved endpoint")
        try:
            status, content, _ = self.execute_egress(url=endpoint, method=str(operation.get("method", "POST")), headers={"Idempotency-Key": str(row.idempotency_key)}, payload=payload,
                allowed_domains=list(policy.get("allowed_domains", [])), max_request_bytes=int(policy.get("max_request_bytes", 1_000_000)),
                max_response_bytes=int(policy.get("max_response_bytes", 10_000_000)), allowed_mime_types=list(policy.get("allowed_mime_types", ["application/json"])),
                timeout_seconds=int(policy.get("timeout_seconds", 20)), transport=transport)
        except PolicyBlockedError as exc:
            # Transport ambiguity is the only state that must be reconciled;
            # policy errors are definite non-dispatch failures.
            if "unknown" in str(exc).lower():
                self.mark_unknown(invocation_id)
                return "unknown"
            self._finish(invocation_id, completed=False, result_fingerprint=_fingerprint({"error": exc.code}))
            raise
        try:
            value = json.loads(content)
            from src.domain.agent.schema_validation import validate_json_schema
            validate_json_schema(value, operation.get("output_schema", {"type": "object"}))
        except (json.JSONDecodeError, ValidationError_) as exc:
            self._finish(invocation_id, completed=False, result_fingerprint=_fingerprint({"schema_error": str(exc)}))
            raise ValidationError_("Tool response does not satisfy the frozen output schema") from exc
        try:
            value, _policy_version = _sanitize_tool_output(value, (revision.body or {}).get("sanitizer_policy"))
        except PolicyBlockedError as exc:
            self._quarantine_output(invocation_id, reason=exc.code)
            raise
        self._finish(invocation_id, completed=status == "completed", result_fingerprint=_fingerprint(value), result_value=value, output_schema_ref=str(operation.get("output_schema_ref", "tool_output.v1")))
        return status

    def consume_dispatch_event(self, event_id: UUID, *, transport: httpx.Client | None = None) -> str:
        """Worker outbox consumer for exactly one durable Tool side effect."""
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id, with_for_update=True)
            if event is None or event.aggregate_type != "tool_invocation" or event.purpose != "tool_dispatch":
                raise NotFoundError("ToolDispatchOutboxEvent", str(event_id))
            if event.published_at is not None:
                invocation = session.get(ToolInvocationModel, event.aggregate_id)
                return invocation.status if invocation is not None else "published"
            row = session.get(ToolInvocationModel, event.aggregate_id, with_for_update=True)
            if row is None:
                raise NotFoundError("ToolInvocation", str(event.aggregate_id))
            if row.status == "cancelled":
                event.published_at = now
                return "cancelled"
            if row.status == "submitting":
                lease_expires_at = row.dispatch_lease_expires_at
                if lease_expires_at is not None and lease_expires_at.tzinfo is None:
                    lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
                if lease_expires_at is not None and lease_expires_at <= now:
                    # We cannot prove whether the prior process reached the
                    # remote endpoint, so recovery is reconciliation only.
                    row.status = "unknown"
                    row.reconciled_at = now
                    event.published_at = now
                    return "unknown"
                return "leased"
            if row.status != "dispatched":
                raise ConflictError("ToolInvocation is not dispatchable")
            # Persist this fence before network I/O.  A crash in the next
            # instruction will therefore transition to UNKNOWN, never replay.
            row.status = "submitting"
            row.dispatch_lease_owner = f"tool-worker:{uuid4()}"
            row.dispatch_lease_expires_at = now + timedelta(seconds=60)
            row.external_submission_started_at = now
            invocation_id = row.invocation_id
        try:
            result = self.execute_dispatched(invocation_id, transport=transport)
        except PolicyBlockedError:
            # Definitive policy failures are recorded by execute_dispatched;
            # the same external request must never be replayed by this outbox.
            result = "failed"
        with self._factory.begin() as session:
            event = session.get(OutboxEventModel, event_id)
            if event is not None:
                event.published_at = datetime.now(timezone.utc)
        return result

    def _finish(self, invocation_id: UUID, *, completed: bool, result_fingerprint: str,
                result_value: dict[str, Any] | list[Any] | str | int | float | bool | None = None,
                output_schema_ref: str = "tool_output.v1") -> None:
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None:
                raise NotFoundError("ToolInvocation", str(invocation_id))
            if row.status == "cancelled":
                row.late_result_quarantined = True
                return
            if row.status not in {"dispatched", "submitting"}:
                raise ConflictError("Only dispatched ToolInvocation can publish a result")
            row.status = "completed" if completed else "failed"
            row.dispatch_lease_owner = None
            row.dispatch_lease_expires_at = None
            row.actual_cost = float(row.reserved_cost or 0.0)
            row.result_fingerprint = result_fingerprint
            row.reconciled_at = datetime.now(timezone.utc)
            output_id: UUID | None = None
            if completed and row.node_run_attempt_id is not None and result_value is not None:
                schema_id, _, raw_version = output_schema_ref.rpartition(".v")
                if not schema_id or not raw_version.isdigit():
                    raise ValidationError_("Tool output_schema_ref must use schema_id.vN")
                output_id = uuid4()
                session.add(ArtifactVersionModel(artifact_version_id=output_id, artifact_id=uuid4(), schema_id=schema_id,
                    schema_version=int(raw_version), owner_scope=row.owner_scope, content_json={"tool_result": result_value},
                    content_hash=_fingerprint(result_value), lineage_input_refs=[{"tool_invocation_id": str(invocation_id)}], metadata_json={"tool_revision_id": str(row.tool_revision_id), "operation_id": row.operation_id}, created_at=datetime.now(timezone.utc)))
                row.output_artifact_version_id = output_id
            session.add(OutboxEventModel(event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                event_type="tool.result", purpose="tool_result", payload={"status": row.status, "result_fingerprint": result_fingerprint, "output_artifact_version_id": str(output_id) if output_id else None}, created_at=datetime.now(timezone.utc)))
            self._aggregate_parent_attempt(session, row.node_run_attempt_id)

    def _quarantine_output(self, invocation_id: UUID, *, reason: str) -> None:
        """Record a safe fingerprint only; unsafe bytes never enter Artifact storage."""
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id, with_for_update=True)
            if row is None:
                raise NotFoundError("ToolInvocation", str(invocation_id))
            if row.status == "cancelled":
                row.late_result_quarantined = True
                return
            row.status = "quarantined"
            row.late_result_quarantined = True
            row.result_fingerprint = _fingerprint({"quarantine_reason": reason})
            row.reconciled_at = datetime.now(timezone.utc)
            row.dispatch_lease_owner = None
            row.dispatch_lease_expires_at = None
            session.add(OutboxEventModel(
                event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                event_type="tool.security_alert", purpose="tool_security_alert",
                payload={"status": "quarantined", "reason_fingerprint": row.result_fingerprint}, created_at=datetime.now(timezone.utc),
            ))
            session.add(OutboxEventModel(
                event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                event_type="tool.result", purpose="tool_result",
                payload={"status": "quarantined", "result_fingerprint": row.result_fingerprint, "output_artifact_version_id": None}, created_at=datetime.now(timezone.utc),
            ))
            self._aggregate_parent_attempt(session, row.node_run_attempt_id)

    def _aggregate_parent_attempt(self, session: Session, attempt_id: UUID | None) -> None:
        """Advance a paused Agent attempt only after every Tool call settles."""
        if attempt_id is None:
            return
        session.flush()
        pending = session.query(ToolInvocationModel).filter(
            ToolInvocationModel.node_run_attempt_id == attempt_id,
            ToolInvocationModel.status.in_(["authorized", "dispatched", "submitting", "unknown"]),
        ).count()
        if pending:
            return
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
        run = session.get(WorkflowRunModel, node.run_id) if node else None
        if attempt is None or node is None or run is None:
            return
        failed = session.query(ToolInvocationModel).filter(
            ToolInvocationModel.node_run_attempt_id == attempt_id,
            ToolInvocationModel.status.in_(["failed", "cancelled", "quarantined"]),
        ).count()
        attempt.status = AttemptStatus.FAILED if failed else AttemptStatus.COMPLETED
        attempt.completed_at = datetime.now(timezone.utc)
        node.status = NodeRunStatus.FAILED if failed else NodeRunStatus.COMPLETED
        if failed:
            RuntimeService(session_factory=self._factory)._sql_aggregate_run(session, run)
        else:
            RuntimeService(session_factory=self._factory)._sql_schedule_ready(session, run)

    def cancel(self, invocation_id: UUID, *, owner_scope: str) -> None:
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None or row.owner_scope != owner_scope:
                raise ForbiddenError("Tool invocation does not belong to current owner")
            if row.status in {"completed", "cancelled"}:
                return
            row.status = "cancelled"
            row.cancellation_requested_at = datetime.now(timezone.utc)
            session.add(OutboxEventModel(event_id=uuid4(), aggregate_type="tool_invocation", aggregate_id=invocation_id,
                event_type="tool.cancel", purpose="tool_cancel", payload={}, created_at=datetime.now(timezone.utc)))
            self._aggregate_parent_attempt(session, row.node_run_attempt_id)

    def mark_unknown(self, invocation_id: UUID) -> None:
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None:
                raise NotFoundError("ToolInvocation", str(invocation_id))
            if row.status == "cancelled":
                row.late_result_quarantined = True
                return
            row.status = "unknown"
            row.dispatch_lease_owner = None
            row.dispatch_lease_expires_at = None

    def recover_expired_dispatches(self) -> int:
        """Fence crashed submissions as UNKNOWN without repeating egress."""
        now = datetime.now(timezone.utc)
        recovered = 0
        with self._factory.begin() as session:
            rows = list(session.query(ToolInvocationModel).filter(
                ToolInvocationModel.status == "submitting",
                ToolInvocationModel.dispatch_lease_expires_at.is_not(None),
                ToolInvocationModel.dispatch_lease_expires_at <= now,
            ).with_for_update())
            for row in rows:
                row.status = "unknown"
                row.reconciled_at = now
                row.dispatch_lease_owner = None
                row.dispatch_lease_expires_at = None
                event = session.query(OutboxEventModel).filter(
                    OutboxEventModel.aggregate_type == "tool_invocation",
                    OutboxEventModel.aggregate_id == row.invocation_id,
                    OutboxEventModel.purpose == "tool_dispatch",
                    OutboxEventModel.published_at.is_(None),
                ).first()
                if event is not None:
                    event.published_at = now
                recovered += 1
        return recovered

    def reconcile(self, invocation_id: UUID, *, result_fingerprint: str, completed: bool) -> None:
        with self._factory.begin() as session:
            row = session.get(ToolInvocationModel, invocation_id)
            if row is None:
                raise NotFoundError("ToolInvocation", str(invocation_id))
            if row.status == "cancelled":
                row.late_result_quarantined = True
                row.reconciled_at = datetime.now(timezone.utc)
                return
            if row.status != "unknown":
                raise ConflictError("Only unknown ToolInvocation may be reconciled")
            row.status = "completed" if completed else "failed"
            row.result_fingerprint = result_fingerprint
            row.reconciled_at = datetime.now(timezone.utc)
            self._aggregate_parent_attempt(session, row.node_run_attempt_id)

    def decrypt_for_dispatch(self, binding_id: UUID) -> bytes:
        """Internal-only, intentionally absent from public API responses/logs."""
        if self._fernet is None:
            raise PolicyBlockedError("凭证加密密钥未配置")
        with self._factory() as session:
            row = session.get(CredentialBindingModel, binding_id)
            if row is None or row.status != "active":
                raise PolicyBlockedError("凭证不可用")
            try:
                return self._fernet.decrypt(row.encrypted_secret.encode())
            except InvalidToken as exc:
                raise PolicyBlockedError("凭证密文无效") from exc

    @staticmethod
    def validate_egress_url(url: str, allowed_domains: list[str]) -> str:
        """Fail closed before a Tool transport is allowed to leave the process.

        Callers must resolve DNS in their hardened transport and re-run this
        check on every redirect target; literal private/link-local addresses
        are rejected here rather than delegated to a connector.
        """
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise PolicyBlockedError("Tool egress URL is not an approved HTTPS origin")
        host = parsed.hostname.rstrip(".").lower()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if not any(host == domain.lower() or host.endswith(f".{domain.lower()}") for domain in allowed_domains):
                raise PolicyBlockedError("Tool egress domain is not allowlisted")
        else:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise PolicyBlockedError("Tool egress cannot target a private or reserved address")
            raise PolicyBlockedError("Tool egress requires an allowlisted DNS name")
        return url

    @classmethod
    def execute_egress(
        cls, *, url: str, method: str, headers: dict[str, str], payload: bytes,
        allowed_domains: list[str], max_request_bytes: int, max_response_bytes: int,
        allowed_mime_types: list[str], timeout_seconds: int = 20, transport: httpx.Client | None = None,
    ) -> tuple[str, bytes, str]:
        """Perform one bounded, redirect-disabled tool request.

        A production connector must use this boundary rather than calling an
        HTTP client directly. Unknown transport state is surfaced as a stable
        reconciliation state, never silently retried by this method.
        """
        cls.validate_egress_url(url, allowed_domains)
        if len(payload) > max_request_bytes:
            raise PolicyBlockedError("Tool request exceeds its declared size limit")
        host = urlparse(url).hostname
        assert host is not None
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise PolicyBlockedError("Tool egress DNS resolution failed") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise PolicyBlockedError("Tool egress DNS resolved to a prohibited address")
        client = transport or httpx.Client(follow_redirects=False, timeout=float(timeout_seconds))
        try:
            response = client.request(method, url, headers=headers, content=payload, follow_redirects=False)
        except httpx.RequestError as exc:
            raise PolicyBlockedError("Tool invocation outcome is unknown; reconciliation required") from exc
        if 300 <= response.status_code < 400:
            raise PolicyBlockedError("Tool redirects are prohibited")
        # DNS must still resolve to the same public addresses after the
        # connection.  For the production HTTP transport, also verify the
        # actual peer; this closes the resolve-then-connect rebinding gap.
        try:
            resolved_after = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise PolicyBlockedError("Tool egress DNS revalidation failed") from exc
        if resolved_after != addresses:
            raise PolicyBlockedError("Tool egress DNS changed during connection")
        peer_ip: str | None = None
        raw_peer = response.extensions.get("peer_ip") or response.extensions.get("server_addr")
        if isinstance(raw_peer, tuple):
            raw_peer = raw_peer[0]
        if isinstance(raw_peer, str):
            peer_ip = raw_peer
        stream = response.extensions.get("network_stream")
        if peer_ip is None and stream is not None:
            try:
                candidate = stream.get_extra_info("server_addr")
                peer_ip = candidate[0] if isinstance(candidate, tuple) else str(candidate) if candidate else None
            except Exception as exc:
                raise PolicyBlockedError("Tool egress peer verification failed") from exc
        if peer_ip is not None:
            try:
                peer = ipaddress.ip_address(peer_ip)
            except ValueError as exc:
                raise PolicyBlockedError("Tool egress peer address is invalid") from exc
            if peer.is_private or peer.is_loopback or peer.is_link_local or peer.is_reserved or peer.is_multicast or peer_ip not in addresses:
                raise PolicyBlockedError("Tool egress peer is not an approved resolved address")
        elif transport is None:
            raise PolicyBlockedError("Tool egress peer could not be verified")
        mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if allowed_mime_types and mime not in {item.lower() for item in allowed_mime_types}:
            raise PolicyBlockedError("Tool response MIME type is not allowed")
        content = response.content
        if len(content) > max_response_bytes:
            raise PolicyBlockedError("Tool response exceeds its declared size limit")
        if response.status_code >= 400:
            raise PolicyBlockedError("Tool provider rejected the request")
        return "completed", content, mime
