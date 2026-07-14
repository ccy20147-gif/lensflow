"""Durable AtlasCloud execution for a frozen AgentRevision."""
from __future__ import annotations

import hashlib
import json
from typing import Any
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ForbiddenError, PolicyBlockedError, ValidationError_
from src.domain.agent.agent_compiler import compile_agent
from src.domain.agent.schema_validation import validate_json_schema
from src.domain.agent.tool_broker import ToolBroker
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown
from src.domain.runtime.runtime_service import RuntimeService
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.skill_repository import SqlSkillRepository
from src.infra.db.session import get_session_factory
from src.infra.db.models import ArtifactVersionModel, NodeRunAttemptModel, NodeRunModel, WorkflowRunModel
from src.schemas.models import OwnerScope, ResourceRef


class AgentInvocationService:
    def __init__(self, factory: sessionmaker[Session] | None = None, *, adapter: AtlasCloudAdapter | None = None,
                 tool_broker: ToolBroker | None = None) -> None:
        self._factory = factory or get_session_factory()
        self._agents = SqlAgentRepository(self._factory)
        self._runtime = RuntimeService(session_factory=self._factory)
        self._adapter = adapter or AtlasCloudAdapter()
        self._tool_broker = tool_broker or ToolBroker(self._factory)

    def execute(self, *, agent_revision_id: UUID, owner_scope: OwnerScope, node_run_attempt_id: UUID,
                typed_inputs: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        # The worker normally calls this service, but it is still a provider
        # boundary. Bind the supplied owner to the durable attempt before a
        # trace, outbox row, or AtlasCloud submission can be created.
        with self._factory() as session:
            attempt = session.get(NodeRunAttemptModel, node_run_attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            run = session.get(WorkflowRunModel, node.run_id) if node else None
            if attempt is None or node is None or run is None:
                raise ValidationError_("AgentInvoke requires a durable parent NodeRunAttempt")
            if run.owner_scope != owner_scope.scoped_id:
                raise ForbiddenError("AgentInvoke attempt belongs to a different owner_scope")
        revision = self._agents.get_revision(agent_revision_id)
        definition = self._agents.get_definition_for_revision(agent_revision_id)
        if definition.owner_scope != owner_scope.scoped_id:
            raise ForbiddenError("AgentInvoke owner_scope does not match frozen AgentDefinition")
        if revision.revision_status.value != "active":
            raise ValidationError_("AgentInvoke requires an active frozen AgentRevision")
        plan = compile_agent(revision.model_dump(mode="json", exclude={"revision_id", "revision_number", "content_hash", "revision_status", "agent_kind"}))
        input_resource_refs = self.validate_typed_input_refs(typed_inputs, owner_scope)
        # Reassemble the exact frozen Skill revisions at invocation time.  A
        # retired/revoked dependency blocks new execution, while historical
        # traces keep their already persisted assembly fingerprint.
        assembly = None
        if revision.skill_revision_refs:
            assembly = SqlSkillRepository(self._factory).assemble(
                agent_revision_id=agent_revision_id,
                skill_ids=revision.skill_revision_refs,
                token_budget=int(revision.execution_policy.get("max_skill_tokens", 4096)),
                owner_scope=owner_scope.scoped_id,
            )
        self._persist_trial_trace(
            attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
            agent_revision_id=agent_revision_id, phase="started",
            payload={"sop_steps": [{"step_id": step.step_id, "status": "planned"} for step in revision.sop_steps],
                     "provider": "atlascloud", "model": str(plan["provider_ref"]),
                     "skill_assembly_plan_id": str(assembly.plan_id) if assembly else None,
                     "skill_assembly_fingerprint": assembly.final_context_hash if assembly else None,
                     "input_resource_refs": input_resource_refs,
                     "input_fingerprint": hashlib.sha256(json.dumps(typed_inputs, sort_keys=True, default=str).encode()).hexdigest()},
        )
        if not self._adapter.configured:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        model_id = str(plan["provider_ref"]).split("/", 1)[1]
        request = {"messages": [{"role": "user", "content": json.dumps(typed_inputs, sort_keys=True)}]}
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        provider_attempt, _ = self._runtime.dispatch_provider(node_run_attempt_id, provider_id="atlascloud", model_id=model_id,
            idempotency_key=idempotency_key, request_body_hash=request_hash)
        try:
            submission = self._adapter.submit(operation="llm", model_id=model_id, payload=request, idempotency_key=idempotency_key)
        except AtlasSubmissionUnknown:
            self._runtime.mark_provider_unknown(provider_attempt.provider_attempt_id)
            self._persist_trial_trace(attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
                agent_revision_id=agent_revision_id, phase="unknown",
                payload={"provider_attempt_id": str(provider_attempt.provider_attempt_id), "reason": "atlas_submission_outcome_unknown"})
            return {"status": "unknown", "provider_attempt_id": provider_attempt.provider_attempt_id}
        if submission.task_id:
            self._runtime.bind_provider_task(provider_attempt.provider_attempt_id, submission.task_id)
        outputs = submission.outputs
        if not outputs or not all(isinstance(output, dict) for output in outputs):
            self._runtime.fail_attempt(node_run_attempt_id)
            self._persist_trial_trace(attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
                agent_revision_id=agent_revision_id, phase="failed",
                payload={"failure_owner": "provider_output_contract", "provider_attempt_id": str(provider_attempt.provider_attempt_id)})
            raise ValidationError_("AtlasCloud Agent output does not satisfy the fixed object schema")
        tool_calls = self._extract_tool_calls(outputs)
        if tool_calls:
            bindings = typed_inputs.get("tool_bindings", {})
            if not isinstance(bindings, dict):
                self._runtime.fail_attempt(node_run_attempt_id)
                raise ValidationError_("Agent tool_bindings must be an object keyed by frozen ToolRevision ID")
            allowed = {str(value) for value in revision.tool_revision_refs}
            dispatched: list[dict[str, str]] = []
            seen: set[str] = set()
            for index, call in enumerate(tool_calls):
                revision_id = str(call.get("tool_revision_id", ""))
                if revision_id not in allowed or revision_id in seen:
                    self._runtime.fail_attempt(node_run_attempt_id)
                    raise ValidationError_(f"Provider requested unapproved or duplicate ToolRevision at tool_calls[{index}]")
                binding_id = bindings.get(revision_id)
                try:
                    from uuid import UUID as _UUID
                    invocation = self._tool_broker.authorize_and_record(
                        binding_id=_UUID(str(binding_id)), owner_scope=owner_scope.scoped_id, tool_revision_id=_UUID(revision_id),
                        operation_id=str(call.get("operation_id", "")), requested_scopes=list(call.get("requested_scopes", [])),
                        tool_input=dict(call.get("input", {})), disclosure_fields=list(call.get("disclosure_fields", [])),
                        usage={"agent_revision_id": str(agent_revision_id), "provider_attempt_id": str(provider_attempt.provider_attempt_id)},
                        dispatch=True, node_run_attempt_id=node_run_attempt_id, agent_revision_id=agent_revision_id,
                    )
                except (TypeError, ValueError, ValidationError_) as exc:
                    self._runtime.fail_attempt(node_run_attempt_id)
                    raise ValidationError_(f"Invalid structured ToolCall at tool_calls[{index}]") from exc
                assert isinstance(invocation, tuple)
                seen.add(revision_id)
                dispatched.append({"tool_invocation_id": str(invocation[0]), "dispatch_event_id": str(invocation[1])})
            # Tool results reconcile back into this frozen attempt; it may not
            # publish an Agent result or advance downstream nodes meanwhile.
            from src.schemas.enums import AttemptStatus
            with self._factory.begin() as session:
                parent = session.get(NodeRunAttemptModel, node_run_attempt_id)
                if parent is not None:
                    parent.status = AttemptStatus.WAITING_EXTERNAL
            self._persist_trial_trace(attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
                agent_revision_id=agent_revision_id, phase="waiting_tool",
                payload={"provider_attempt_id": str(provider_attempt.provider_attempt_id),
                         "tool_disclosures": dispatched})
            return {"status": "waiting_tool", "agent_revision_id": agent_revision_id,
                    "provider_attempt_id": provider_attempt.provider_attempt_id, "tool_dispatches": dispatched}
        output_schema = revision.output_schema
        if output_schema is not None:
            try:
                for output in outputs:
                    validate_json_schema(output, output_schema)
            except ValidationError_:
                self._runtime.fail_attempt(node_run_attempt_id)
                self._persist_trial_trace(attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
                    agent_revision_id=agent_revision_id, phase="failed",
                    payload={"failure_owner": "output_schema", "provider_attempt_id": str(provider_attempt.provider_attempt_id)})
                raise
        schema_ref = revision.output_schema_ref or "agent_output.v1"
        schema_id, _, version_suffix = schema_ref.rpartition(".v")
        if not schema_id or not version_suffix.isdigit():
            self._runtime.fail_attempt(node_run_attempt_id)
            raise ValidationError_("Agent output_schema_ref must use schema_id.vN")
        record, _, artifact_ids = self._runtime.publish_provider_json_outputs(provider_attempt.provider_attempt_id,
            owner_scope=owner_scope.scoped_id, schema_id=schema_id, schema_version=int(version_suffix), outputs=outputs,
            model_version=submission.model_version, response_fingerprint=submission.raw_fingerprint,
            usage=submission.usage, actual_cost=submission.actual_cost)
        self._persist_trial_trace(attempt_id=node_run_attempt_id, owner_scope=owner_scope.scoped_id,
            agent_revision_id=agent_revision_id, phase="completed",
            payload={"provider_attempt_id": str(provider_attempt.provider_attempt_id), "record_id": str(record.record_id),
                     "usage": submission.usage, "actual_cost": submission.actual_cost,
                     "output_artifact_version_ids": [str(item) for item in artifact_ids]})
        return {"status": "completed", "agent_revision_id": agent_revision_id, "record_id": record.record_id, "artifact_version_ids": artifact_ids}

    def validate_typed_input_refs(self, typed_inputs: dict[str, Any], owner_scope: OwnerScope) -> list[dict[str, str | None]]:
        """Re-check every fixed input ref before prepare or execution.

        Artifact versions are private to their owner.  A foreign input must be
        a complete ResourceRef, and the live grant must explicitly carry the
        reference action.  The returned IDs are safe audit metadata; prompt
        content and resource bodies never enter the trace.
        """
        artifacts = SqlArtifactRepository(self._factory)
        resources = SqlResourceRepository(self._factory)
        resolved: list[dict[str, str | None]] = []

        def walk(value: Any, path: str) -> None:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")
                return
            if not isinstance(value, dict):
                return
            if {"resource_id", "resource_type", "revision_id"}.issubset(value):
                try:
                    ref = ResourceRef.model_validate(value)
                except Exception as exc:
                    raise ValidationError_("Typed input ResourceRef is malformed", details={"field": path}) from exc
                # Resource repository verifies identity, active revision,
                # owner/grantee and the current non-revoked grant state.
                resources.resolve_ref(ref.resource_id, ref.revision_id, owner_scope, ref.grant_snapshot_id,
                                      required_actions={"reference"})
                resolved.append({
                    "resource_id": str(ref.resource_id), "resource_type": ref.resource_type,
                    "revision_id": str(ref.revision_id),
                    "grant_snapshot_id": str(ref.grant_snapshot_id) if ref.grant_snapshot_id else None,
                })
                return
            if "artifact_version_id" in value:
                try:
                    artifacts.get_version(UUID(str(value["artifact_version_id"])), owner_scope)
                except (TypeError, ValueError) as exc:
                    raise ValidationError_("Typed input ArtifactRef is malformed", details={"field": path}) from exc
                return
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))

        walk(typed_inputs, "typed_inputs")
        return resolved

    def _persist_trial_trace(self, *, attempt_id: UUID, owner_scope: str,
                             agent_revision_id: UUID, phase: str, payload: dict[str, Any]) -> None:
        """Append a scrubbed, immutable SOP trace for trial and production runs.

        A trace is deliberately an ArtifactVersion instead of a mutable log so
        refresh/restart and later RequestInput recovery cannot erase the
        execution facts.  The browser receives IDs, disclosed tool metadata and
        usage only; typed prompt contents and credential bindings never enter
        this artifact.
        """
        with self._factory.begin() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            run = session.get(WorkflowRunModel, node.run_id) if node else None
            if attempt is None or node is None or run is None or run.owner_scope != owner_scope:
                # The runtime owns association validation; do not fabricate a
                # trial trace for an orphaned or cross-owner attempt.
                return
            body = {"attempt_id": str(attempt_id), "agent_revision_id": str(agent_revision_id),
                    "phase": phase, "occurred_at": datetime.now(timezone.utc).isoformat(), **payload}
            session.add(ArtifactVersionModel(
                artifact_version_id=uuid4(), artifact_id=uuid4(), schema_id="toonflow.agent_sop_trace",
                schema_version=1, owner_scope=owner_scope, content_json=body,
                content_hash=hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest(),
                created_by_run_id=run.run_id, lineage_input_refs=[{"node_run_attempt_id": str(attempt_id)}],
                metadata_json={"agent_revision_id": str(agent_revision_id), "phase": phase},
                created_at=datetime.now(timezone.utc),
            ))

    @staticmethod
    def _extract_tool_calls(outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Accept only explicit provider structured calls, never model prose."""
        calls: list[dict[str, Any]] = []
        for output in outputs:
            raw = output.get("tool_calls")
            if raw is None:
                continue
            if not isinstance(raw, list):
                raise ValidationError_("Provider tool_calls must be an array")
            for call in raw:
                if not isinstance(call, dict) or not isinstance(call.get("input", {}), dict):
                    raise ValidationError_("Provider ToolCall must be a typed object")
                calls.append(call)
        return calls
