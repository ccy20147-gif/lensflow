"""Durable expansion of a frozen Media Recipe DAG into runtime operator work."""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.recipe.media_recipe_compiler import compile_media_recipe
from src.infra.db.models import (
    NodeRunAttemptModel, NodeRunModel, ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel, ProviderOutputBindingModel, WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown


class RecipeRuntimeService:
    """Expand each immutable operator into an independently recoverable attempt."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    def materialize(self, *, parent_attempt_id: UUID, body: dict[str, Any], inputs: dict[str, Any]) -> list[UUID]:
        compiled = compile_media_recipe(body)
        plan = compiled["compiled_plan"]
        with self._factory.begin() as session:
            parent = session.get(NodeRunAttemptModel, parent_attempt_id)
            parent_node = session.get(NodeRunModel, parent.node_run_id) if parent else None
            run = session.get(WorkflowRunModel, parent_node.run_id) if parent_node else None
            if parent is None or parent_node is None or run is None:
                raise NotFoundError("Recipe parent NodeRunAttempt", str(parent_attempt_id))
            existing = list(session.scalars(select(NodeRunAttemptModel.attempt_id).join(NodeRunModel).where(
                NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id.like(f"{parent_node.node_instance_id}:recipe:%"),
            )))
            if existing:
                return existing
            child_attempts: list[UUID] = []
            child_nodes: list[tuple[NodeRunModel, dict[str, Any]]] = []
            for step in plan["steps"]:
                node = NodeRunModel(node_run_id=uuid4(), run_id=run.run_id, node_instance_id=f"{parent_node.node_instance_id}:recipe:{step['id']}",
                    node_type_id=f"recipe.{step['operator']}", status=NodeRunStatus.READY if not step.get("depends_on") else NodeRunStatus.PENDING)
                session.add(node)
                child_nodes.append((node, step))
            # The database has real FK constraints; child node identities must
            # exist before their attempts are inserted.
            session.flush()
            for order, (node, step) in enumerate(child_nodes):
                attempt = NodeRunAttemptModel(attempt_id=uuid4(), node_run_id=node.node_run_id, attempt_number=1, execution_epoch=1,
                    fixed_input={"recipe_parent_attempt_id": str(parent_attempt_id), "operator": step, "recipe_order": order,
                                 "root_inputs": inputs, "recipe_body": body, "recipe_plan_hash": compiled["plan_hash"]},
                    status=AttemptStatus.PENDING)
                session.add(attempt)
                child_attempts.append(attempt.attempt_id)
            parent.status = AttemptStatus.WAITING_EXTERNAL
            session.flush()
            return child_attempts

    def complete_internal(self, attempt_id: UUID, *, external: bool = False) -> None:
        """Complete a non-provider operator and unlock every satisfied DAG child."""
        with self._factory.begin() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            if attempt is None or node is None:
                raise NotFoundError("Recipe operator attempt", str(attempt_id))
            step = dict((attempt.fixed_input or {}).get("operator", {}))
            if not external and step.get("operator") in {"atlas_llm", "atlas_image", "atlas_video"}:
                raise ValidationError_("External Recipe operator requires AtlasCloud dispatch")
            attempt.status = AttemptStatus.COMPLETED
            node.status = NodeRunStatus.COMPLETED
            self._unlock_satisfied_children(session, attempt, node)

    def dispatch_external(self, attempt_id: UUID, *, adapter: AtlasCloudAdapter, idempotency_key: str) -> dict[str, Any]:
        """Dispatch exactly one frozen Atlas operator from its child attempt."""
        with self._factory() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            if attempt is None:
                raise NotFoundError("Recipe operator attempt", str(attempt_id))
            step = dict((attempt.fixed_input or {}).get("operator", {}))
        operation = {"atlas_llm": "llm", "atlas_image": "image", "atlas_video": "video"}.get(str(step.get("operator")))
        if operation is None or not step.get("model_id"):
            raise ValidationError_("Recipe child is not a configured Atlas operator")
        runtime = RuntimeService(session_factory=self._factory)
        provider, outbox = runtime.dispatch_provider(attempt_id, provider_id="atlascloud", model_id=str(step["model_id"]), idempotency_key=idempotency_key, request_body_hash="recipe:" + str(attempt_id))
        try:
            result = adapter.submit(operation=operation, model_id=str(step["model_id"]), payload={"input": (attempt.fixed_input or {}).get("root_inputs", {}), "parameters": step.get("parameters", {})}, idempotency_key=idempotency_key)
        except AtlasSubmissionUnknown:
            runtime.mark_provider_unknown(provider.provider_attempt_id)
            return {"status": "unknown", "provider_attempt_id": provider.provider_attempt_id, "outbox_event_id": outbox.event_id}
        if result.task_id:
            runtime.bind_provider_task(provider.provider_attempt_id, result.task_id)
        return {"status": "submitted", "provider_attempt_id": provider.provider_attempt_id, "outbox_event_id": outbox.event_id}

    def publish_external_result(self, *, provider_attempt_id: UUID, owner_scope: str, outputs: list[dict[str, Any]], model_version: str, fingerprint: str, usage: dict[str, Any], cost: float) -> list[UUID]:
        """Publish an external operator result then advance its DAG child."""
        runtime = RuntimeService(session_factory=self._factory)
        record, _, artifacts = runtime.publish_provider_json_outputs(provider_attempt_id, owner_scope=owner_scope,
            schema_id="media_output", schema_version=1, outputs=outputs, model_version=model_version,
            response_fingerprint=fingerprint, usage=usage, actual_cost=cost)
        with self._factory() as session:
            provider_attempt = session.get(ProviderInvocationAttemptModel, provider_attempt_id)
            if provider_attempt is None:
                raise NotFoundError("ProviderInvocationAttempt", str(provider_attempt_id))
            child_id = provider_attempt.node_run_attempt_id
        self.advance_completed_child(child_id)
        return artifacts

    def advance_completed_child(self, attempt_id: UUID) -> None:
        """Unlock all operators whose frozen predecessors have completed."""
        with self._factory.begin() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            if attempt is None or node is None or attempt.status != AttemptStatus.COMPLETED:
                raise ConflictError("Recipe child must have a published completed result")
            self._unlock_satisfied_children(session, attempt, node)

    @staticmethod
    def _unlock_satisfied_children(session: Session, completed: NodeRunAttemptModel, node: NodeRunModel) -> None:
        """Materialize DAG readiness from frozen ``depends_on``, never list order."""
        prefix = node.node_instance_id.rsplit(":recipe:", 1)[0] + ":recipe:"
        attempts = list(session.scalars(select(NodeRunAttemptModel).join(NodeRunModel).where(
            NodeRunModel.run_id == node.run_id, NodeRunModel.node_instance_id.like(f"{prefix}%"),
        )))
        by_step = {str((item.fixed_input or {}).get("operator", {}).get("id", "")): item for item in attempts}
        for child in attempts:
            child_node = session.get(NodeRunModel, child.node_run_id)
            if child_node is None or child_node.status != NodeRunStatus.PENDING:
                continue
            dependencies = (child.fixed_input or {}).get("operator", {}).get("depends_on", [])
            if not isinstance(dependencies, list):
                raise ConflictError("Frozen recipe operator depends_on is invalid")
            if all(dependency in by_step and by_step[dependency].status == AttemptStatus.COMPLETED for dependency in dependencies):
                child_node.status = NodeRunStatus.READY
        parent_raw = str((completed.fixed_input or {}).get("recipe_parent_attempt_id", ""))
        if not parent_raw or not attempts or not all(item.status == AttemptStatus.COMPLETED for item in attempts):
            return
        parent = session.get(NodeRunAttemptModel, UUID(parent_raw))
        parent_node = session.get(NodeRunModel, parent.node_run_id) if parent else None
        if parent is not None and parent_node is not None:
            # The outer MediaRecipeInvoke exposes only the frozen public
            # output contract. Internal nodes remain trace-only, while their
            # immutable provider ArtifactVersions are aggregated here for
            # downstream workflow scheduling and replay lineage.
            output_ids = list(session.scalars(
                select(ProviderOutputBindingModel.output_artifact_version_id)
                .join(ProviderInvocationRecordModel, ProviderInvocationRecordModel.record_id == ProviderOutputBindingModel.record_id)
                .join(ProviderInvocationAttemptModel, ProviderInvocationAttemptModel.provider_attempt_id == ProviderInvocationRecordModel.provider_attempt_id)
                .where(ProviderInvocationAttemptModel.node_run_attempt_id.in_([item.attempt_id for item in attempts]))
                .order_by(ProviderOutputBindingModel.output_index)
            ))
            fixed_parent = dict(parent.fixed_input or {})
            fixed_parent["recipe_output_artifact_version_ids"] = [str(value) for value in output_ids]
            parent.fixed_input = fixed_parent
            parent.status, parent_node.status = AttemptStatus.COMPLETED, NodeRunStatus.COMPLETED

    def fail_child(self, attempt_id: UUID, *, policy: str = "fail_fast") -> None:
        """Record partial failure without deleting prior operator artifacts."""
        with self._factory.begin() as session:
            attempt = session.get(NodeRunAttemptModel, attempt_id)
            node = session.get(NodeRunModel, attempt.node_run_id) if attempt else None
            if attempt is None or node is None:
                raise NotFoundError("Recipe operator attempt", str(attempt_id))
            attempt.status = AttemptStatus.FAILED
            node.status = NodeRunStatus.FAILED
            parent_id = UUID(str((attempt.fixed_input or {})["recipe_parent_attempt_id"]))
            parent = session.get(NodeRunAttemptModel, parent_id)
            if parent is None:
                raise NotFoundError("Recipe parent attempt", str(parent_id))
            fixed = dict(parent.fixed_input or {})
            failures = list(fixed.get("recipe_operator_failures", []))
            failures.append({"attempt_id": str(attempt_id), "operator": (attempt.fixed_input or {}).get("operator", {}).get("id")})
            fixed["recipe_operator_failures"] = failures
            parent.fixed_input = fixed
            if policy == "fail_fast":
                parent.status = AttemptStatus.FAILED
                parent_node = session.get(NodeRunModel, parent.node_run_id)
                if parent_node is not None:
                    parent_node.status = NodeRunStatus.FAILED
            elif policy != "collect_errors":
                raise ValidationError_("Recipe failure_policy must be fail_fast or collect_errors")

    def fallback_attempt(self, attempt_id: UUID) -> UUID:
        """Create a new fenced attempt after rechecking its frozen recipe plan."""
        with self._factory.begin() as session:
            old = session.get(NodeRunAttemptModel, attempt_id)
            if old is None or old.status != AttemptStatus.FAILED:
                raise ConflictError("Recipe fallback requires a failed child after unknown reconciliation")
            fixed = dict(old.fixed_input or {})
            body = fixed.get("recipe_body")
            step_id = str(fixed.get("operator", {}).get("id", ""))
            if not isinstance(body, dict) or not step_id:
                raise ConflictError("Recipe fallback lacks a frozen recipe plan")
            recompiled = compile_media_recipe(body)
            replacement = next((step for step in recompiled["compiled_plan"]["steps"] if step["id"] == step_id), None)
            if replacement is None:
                raise ConflictError("Recipe fallback operator is absent from recompiled plan")
            # Recompile is also the capability/policy gate.  Persist the
            # resulting snapshot and fresh cost estimate for audit rather than
            # silently reusing a stale external request contract.
            fixed["operator"] = replacement
            fixed["recipe_plan_hash"] = recompiled["plan_hash"]
            fixed["fallback_recheck"] = {
                "plan_hash": recompiled["plan_hash"],
                "capability_snapshot": replacement.get("capability_snapshot", {}),
                "provider_policy": "atlascloud-only",
                "cost_estimate": replacement.get("parameters", {}).get("estimated_cost", 0),
            }
            new = NodeRunAttemptModel(attempt_id=uuid4(), node_run_id=old.node_run_id,
                attempt_number=int(old.attempt_number or 1) + 1, execution_epoch=int(old.execution_epoch or 1) + 1,
                fixed_input=fixed, status=AttemptStatus.PENDING)
            session.add(new)
            return new.attempt_id
