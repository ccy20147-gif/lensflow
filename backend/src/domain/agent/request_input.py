"""Durable, agent-owned RequestInput lifecycle.

RequestInput is intentionally separate from workflow-owned Human Gate.  It
uses the same durable HumanTask aggregate but records the answer on the fixed
attempt input and only resumes that exact attempt.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactVersionModel,
    AgentDefinitionModel, AgentRevisionModel, HumanTaskModel, NodeRunAttemptModel,
    NodeRunModel, OutboxEventModel, WorkflowRunModel, HumanTaskDecisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, HumanTaskStatus, NodeRunStatus, RunStatus


class AgentRequestInputService:
    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    def create(
        self, *, agent_revision_id: UUID, run_id: UUID, node_run_id: UUID, attempt_id: UUID,
        schema_ref: str, question: str, timeout_minutes: int, idempotency_token: str,
        input_schema: dict[str, Any], requester_scope: str,
    ) -> HumanTaskModel:
        if not schema_ref or not question.strip() or timeout_minutes < 1 or not idempotency_token or not isinstance(input_schema, dict):
            raise ValidationError_("RequestInput requires schema_ref, question, positive timeout and idempotency token")
        if input_schema.get("type") not in {None, "object"}:
            raise ValidationError_("RequestInput schema must be an object schema")
        max_response_bytes = int(input_schema.get("max_response_bytes", 16_384))
        if max_response_bytes < 1 or max_response_bytes > 1_000_000:
            raise ValidationError_("RequestInput max_response_bytes is outside allowed bounds")
        with self._factory.begin() as s:
            revision = s.get(AgentRevisionModel, agent_revision_id)
            if revision is None or revision.status != "active":
                raise ValidationError_("RequestInput requires an active frozen AgentRevision")
            definition = s.get(AgentDefinitionModel, revision.agent_id)
            run = s.get(WorkflowRunModel, run_id)
            node = s.get(NodeRunModel, node_run_id)
            attempt = s.get(NodeRunAttemptModel, attempt_id)
            if not definition or not run or not node or not attempt or node.run_id != run_id or attempt.node_run_id != node_run_id:
                raise ValidationError_("RequestInput run/node/attempt association is invalid")
            if definition.owner_scope != run.owner_scope:
                raise ValidationError_("RequestInput Agent owner_scope does not match run")
            if run.owner_scope != requester_scope:
                raise ConflictError("Only the workflow owner may create Agent RequestInput")
            prior = s.query(HumanTaskModel).filter(
                HumanTaskModel.attempt_id == attempt_id,
                HumanTaskModel.task_kind == "request_input",
            ).first()
            if prior is not None:
                if (prior.timeout_policy or {}).get("creation_idempotency_token") == idempotency_token:
                    return prior
                raise ConflictError("RequestInput already exists for this attempt")
            task = HumanTaskModel(
                task_id=uuid4(), task_kind="request_input", owner_layer="agent",
                owner_revision_id=agent_revision_id, run_id=run_id, node_run_id=node_run_id,
                # RequestInput is not a workflow Gate.  It is represented as
                # advisory in the shared task enum while owner_layer=agent is
                # the policy discriminator that prevents it becoming a Gate.
                attempt_id=attempt_id, input_snapshot_refs=[], policy_strength="advisory",
                schema_ref=schema_ref, timeout_policy={"duration_minutes": timeout_minutes, "question": question, "creation_idempotency_token": idempotency_token, "input_schema": input_schema, "max_response_bytes": max_response_bytes},
                status=HumanTaskStatus.WAITING, task_version=1, created_at=datetime.now(timezone.utc),
            )
            node.status = NodeRunStatus.WAITING_USER
            run.status = RunStatus.WAITING_USER
            s.add(task)
            s.add(OutboxEventModel(event_id=uuid4(), aggregate_type="human_task", aggregate_id=task.task_id,
                event_type="request_input.created", purpose="request_input", payload={"attempt_id": str(attempt_id), "schema_ref": schema_ref}))
            trace = {"attempt_id": str(attempt_id), "agent_revision_id": str(agent_revision_id), "phase": "waiting_user",
                     "task_id": str(task.task_id), "schema_ref": schema_ref, "question_hash": hashlib.sha256(question.encode()).hexdigest()}
            s.add(ArtifactVersionModel(artifact_version_id=uuid4(), artifact_id=uuid4(), schema_id="toonflow.agent_sop_trace",
                schema_version=1, owner_scope=requester_scope, content_json=trace,
                content_hash=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest(), created_by_run_id=run_id,
                lineage_input_refs=[{"node_run_attempt_id": str(attempt_id)}], metadata_json={"phase": "waiting_user"}, created_at=datetime.now(timezone.utc)))
            s.flush()
            return task

    def resolve(self, *, task_id: UUID, task_version: int, idempotency_token: str, answer: dict[str, Any], requester_scope: str) -> HumanTaskModel:
        if not isinstance(answer, dict) or not idempotency_token:
            raise ValidationError_("RequestInput answer must be a typed object with an idempotency token")
        with self._factory.begin() as s:
            task = s.get(HumanTaskModel, task_id, with_for_update=True)
            if task is None or task.task_kind != "request_input" or task.owner_layer != "agent":
                raise NotFoundError("AgentRequestInput", str(task_id))
            policy = dict(task.timeout_policy or {})
            if task.task_version != task_version:
                raise ConflictError("RequestInput task version does not match")
            prior = s.query(HumanTaskDecisionModel).filter(
                HumanTaskDecisionModel.task_id == task_id,
                HumanTaskDecisionModel.idempotency_token == idempotency_token,
            ).first()
            if prior is not None:
                return task
            if task.status == HumanTaskStatus.ACCEPTED:
                raise ConflictError("RequestInput already has a terminal answer")
            if task.status != HumanTaskStatus.WAITING:
                raise ConflictError("RequestInput is not waiting for an answer")
            attempt = s.get(NodeRunAttemptModel, task.attempt_id)
            node = s.get(NodeRunModel, task.node_run_id)
            run = s.get(WorkflowRunModel, task.run_id)
            if not attempt or not node or not run:
                raise ValidationError_("RequestInput owner attempt no longer exists")
            if run.owner_scope != requester_scope:
                raise ConflictError("Only the workflow owner may resolve Agent RequestInput")
            _validate_typed_answer(answer, dict(policy.get("input_schema") or {}), int(policy.get("max_response_bytes", 16_384)))
            # The answer is preserved only in the fixed input of the exact
            # attempt; its hash is in audit/outbox payload, never logs.
            fixed = dict(attempt.fixed_input or {})
            fixed["request_input"] = {"task_id": str(task_id), "schema_ref": task.schema_ref, "answer": answer}
            attempt.fixed_input = fixed
            attempt.status = AttemptStatus.RUNNING
            node.status = NodeRunStatus.RUNNING
            run.status = RunStatus.RUNNING
            task.status = HumanTaskStatus.ACCEPTED
            # The unique task/version and task/token constraints make a
            # browser retry or competing submitter incapable of advancing the
            # fixed attempt twice.  RequestInput is agent-owned, but its
            # decision is audited with the same durable record as a workflow
            # Human Gate.
            try:
                actor_id = UUID(requester_scope.split(":", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ValidationError_("RequestInput requester scope is invalid") from exc
            s.add(HumanTaskDecisionModel(
                decision_id=uuid4(), task_id=task.task_id, task_version=task.task_version,
                action="submit", actor_id=actor_id, actor_scope=requester_scope,
                typed_payload=answer, notes="", policy_evidence_refs=[],
                idempotency_token=idempotency_token, created_at=datetime.now(timezone.utc),
            ))
            answer_hash = hashlib.sha256(json.dumps(answer, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            s.add(OutboxEventModel(event_id=uuid4(), aggregate_type="human_task", aggregate_id=task.task_id,
                event_type="request_input.resolved", purpose="request_input", payload={"attempt_id": str(task.attempt_id), "answer_hash": answer_hash, "task_version": task_version}))
            trace = {"attempt_id": str(task.attempt_id), "agent_revision_id": str(task.owner_revision_id), "phase": "resumed",
                     "task_id": str(task_id), "schema_ref": task.schema_ref, "answer_hash": answer_hash, "task_version": task_version}
            s.add(ArtifactVersionModel(artifact_version_id=uuid4(), artifact_id=uuid4(), schema_id="toonflow.agent_sop_trace",
                schema_version=1, owner_scope=requester_scope, content_json=trace,
                content_hash=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest(), created_by_run_id=task.run_id,
                lineage_input_refs=[{"node_run_attempt_id": str(task.attempt_id)}], metadata_json={"phase": "resumed"}, created_at=datetime.now(timezone.utc)))
            s.flush()
            return task


def _validate_typed_answer(answer: dict[str, Any], schema: dict[str, Any], max_response_bytes: int) -> None:
    encoded = json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > max_response_bytes:
        raise ValidationError_("RequestInput response exceeds maximum size")
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise ValidationError_("RequestInput schema is invalid")
    for key in required:
        if key not in answer:
            raise ValidationError_(f"RequestInput missing required field: {key}")
    type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}
    for key, value in answer.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected = type_map.get(spec.get("type"))
        if expected and (not isinstance(value, expected) or (spec.get("type") == "integer" and isinstance(value, bool))):
            raise ValidationError_(f"RequestInput field {key} has wrong type")
        if "enum" in spec and value not in spec["enum"]:
            raise ValidationError_(f"RequestInput field {key} is outside allowed choices")
