"""TF-WF-010 generic-node evidence and workflow-owned WorkbenchTask service."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactVersionModel, CandidateSetModel, HumanTaskDecisionModel, HumanTaskModel,
    NodeRunAttemptModel, NodeRunModel, ResourceCommitModel, SelectionRecordModel,
    ResourceDraftModel, ResourceModel, ResourceRevisionModel,
    WorkflowRevisionModel, WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, HumanTaskStatus, NodeRunStatus, RunStatus


BUSINESS_NODE_CATALOG: tuple[dict[str, Any], ...] = (
    {"type_id": "brief", "executor_ref": "business.brief", "output_schema": "creative_brief.v1"},
    {"type_id": "constraint", "executor_ref": "business.constraint", "output_schema": "constraint_report.v1"},
    {"type_id": "structured_generate", "executor_ref": "business.structured_generate", "output_schema": "typed_generated.v1"},
    {"type_id": "model_router", "executor_ref": "business.model_router", "output_schema": "provider_selection.v1"},
    {"type_id": "variants", "executor_ref": "business.variants", "output_schema": "candidate_set.v1"},
    {"type_id": "select_rank", "executor_ref": "business.select_rank", "output_schema": "selection_record.v1"},
    {"type_id": "review", "executor_ref": "business.review", "output_schema": "review_report.v1"},
    {"type_id": "transform", "executor_ref": "business.transform", "output_schema": "transformed_artifact.v1"},
    {"type_id": "workbench_task", "executor_ref": "workflow.workbench_task", "output_schema": "workbench_result.v1"},
    {"type_id": "package_export", "executor_ref": "business.package_export", "output_schema": "package_manifest.v1"},
)


class BusinessNodeService:
    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        return [dict(item) for item in BUSINESS_NODE_CATALOG]

    def create_candidate_set(
        self, *, owner_scope: str, candidate_version_ids: list[UUID], failed_candidates: list[dict[str, Any]],
        cost_allocation: dict[str, Any], run_id: UUID | None = None, node_run_id: UUID | None = None,
    ) -> CandidateSetModel:
        if not candidate_version_ids:
            raise ValidationError_("Variants requires at least one successful candidate")
        if len(set(candidate_version_ids)) != len(candidate_version_ids):
            raise ValidationError_("CandidateSet contains duplicate ArtifactVersion refs")
        with self._factory.begin() as s:
            refs = []
            for version_id in candidate_version_ids:
                artifact = s.get(ArtifactVersionModel, version_id)
                if artifact is None or artifact.owner_scope != owner_scope:
                    raise ForbiddenError("Candidate ArtifactVersion must belong to the workflow owner")
                refs.append({"artifact_id": str(artifact.artifact_id), "artifact_version_id": str(version_id), "schema_id": artifact.schema_id, "schema_version": artifact.schema_version})
            row = CandidateSetModel(candidate_set_id=uuid4(), owner_scope=owner_scope, run_id=run_id, node_run_id=node_run_id,
                candidate_refs=refs, failed_candidates=failed_candidates, cost_allocation=cost_allocation, created_at=datetime.now(timezone.utc))
            s.add(row)
            s.flush()
            return row

    def select(
        self, *, candidate_set_id: UUID, owner_scope: str, ranking: list[UUID], selected_version_ids: list[UUID],
        actor_or_model: str, rubric_revision: str, rationale: str,
    ) -> SelectionRecordModel:
        if not selected_version_ids or not actor_or_model or not rubric_revision:
            raise ValidationError_("SelectionRecord requires selected refs, actor_or_model and rubric_revision")
        with self._factory.begin() as s:
            candidate_set = s.get(CandidateSetModel, candidate_set_id)
            if candidate_set is None:
                raise NotFoundError("CandidateSet", str(candidate_set_id))
            if candidate_set.owner_scope != owner_scope:
                raise ForbiddenError("Only the workflow owner may select candidates")
            known = {str(ref["artifact_version_id"]) for ref in (candidate_set.candidate_refs or [])}
            chosen = {str(value) for value in selected_version_ids}
            ranked = [str(value) for value in ranking]
            if not chosen.issubset(known) or any(value not in known for value in ranked):
                raise ValidationError_("SelectionRecord may only reference its fixed CandidateSet")
            row = SelectionRecordModel(selection_id=uuid4(), candidate_set_id=candidate_set_id, owner_scope=owner_scope,
                ranking=ranked, selected_refs=[ref for ref in candidate_set.candidate_refs if ref["artifact_version_id"] in chosen],
                actor_or_model=actor_or_model, rubric_revision=rubric_revision, rationale=rationale, created_at=datetime.now(timezone.utc))
            s.add(row)
            s.flush()
            return row

    @staticmethod
    def _artifact(
        session: Session, *, owner_scope: str, schema_id: str, content: dict[str, Any],
        run_id: UUID, input_refs: list[dict[str, Any]] | None = None,
    ) -> ArtifactVersionModel:
        """Create the immutable typed output used by every business executor."""
        payload = json.dumps(content, sort_keys=True, separators=(",", ":"))
        row = ArtifactVersionModel(
            artifact_version_id=uuid4(), artifact_id=uuid4(), schema_id=schema_id, schema_version=1,
            owner_scope=owner_scope, content_json=content, content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            content_uri="", blob_uri="", lineage_input_refs=input_refs or [], created_by_run_id=run_id,
            metadata_json={"producer": "workflow.business_node"}, created_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _node_config(revision: WorkflowRevisionModel, node_id: str) -> dict[str, Any]:
        graph = revision.graph if isinstance(revision.graph, dict) else {}
        for item in graph.get("nodes", []):
            if not isinstance(item, dict) or str(item.get("id")) != node_id:
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            config = item.get("config") if isinstance(item.get("config"), dict) else data.get("config", {})
            return dict(config) if isinstance(config, dict) else {}
        return {}

    @staticmethod
    def _validate_json_schema(value: Any, schema: dict[str, Any]) -> None:
        """Small deterministic JSON-schema subset for executable node output.

        The platform's registry owns full schemas.  This boundary deliberately
        handles the invariant required at execution: required properties and
        primitive/object/array shape cannot be silently fabricated.
        """
        expected = schema.get("type")
        types = {"object": dict, "array": list, "string": str, "number": (int, float), "integer": int, "boolean": bool}
        if expected in types and (not isinstance(value, types[expected]) or expected == "boolean" and not isinstance(value, bool)):
            raise ValidationError_("Structured Generate output does not satisfy JSON Schema type")
        if isinstance(value, dict):
            missing = [key for key in schema.get("required", []) if key not in value]
            if missing:
                raise ValidationError_("Structured Generate output is missing required fields", details={"missing": missing})

    def execute_attempt(self, attempt_id: UUID) -> list[ArtifactVersionModel]:
        """Execute one public, non-arbitrary business node from its fixed plan.

        Model calls are deliberately not embedded here: a `variants` node
        receives already-persisted AtlasCloud outputs through the normal
        ProviderInvocation/OutputBinding path, or deterministic draft payloads
        for demo/test workflows.  This executor owns only business semantics,
        typed artifacts, selection evidence and workflow-owned human tasks.
        """
        with self._factory.begin() as s:
            attempt = s.get(NodeRunAttemptModel, attempt_id)
            if attempt is None:
                raise NotFoundError("NodeRunAttempt", str(attempt_id))
            node = s.get(NodeRunModel, attempt.node_run_id)
            if node is None:
                raise NotFoundError("NodeRun", str(attempt.node_run_id))
            run = s.get(WorkflowRunModel, node.run_id)
            revision = s.get(WorkflowRevisionModel, run.workflow_revision_id) if run else None
            if run is None or revision is None:
                raise NotFoundError("WorkflowRun", str(node.run_id))
            if attempt.status not in {AttemptStatus.PENDING, AttemptStatus.LEASED, AttemptStatus.RUNNING}:
                raise ConflictError("Business node attempt is not executable")
            config = self._node_config(revision, node.node_instance_id)
            inputs = dict(attempt.fixed_input or {})
            kind = node.node_type_id
            outputs: list[ArtifactVersionModel] = []

            if kind == "brief":
                content = dict(config.get("brief") or inputs.get("brief") or {})
                if not content:
                    raise ValidationError_("Brief requires fixed creative content")
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="creative_brief", content=content, run_id=run.run_id))
            elif kind == "constraint":
                groups = config.get("constraints", inputs.get("constraints", []))
                if not isinstance(groups, list):
                    raise ValidationError_("Constraint requires a list of constraint objects")
                merged: dict[str, Any] = {}
                conflicts: list[dict[str, Any]] = []
                for group in groups:
                    if not isinstance(group, dict):
                        raise ValidationError_("Constraint item must be an object")
                    for key, value in group.items():
                        if key in merged and merged[key] != value:
                            conflicts.append({"field": key, "values": [merged[key], value]})
                        else:
                            merged[key] = value
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="constraint_report", content={"constraints": merged, "conflicts": conflicts}, run_id=run.run_id))
            elif kind == "structured_generate":
                value = config.get("output", inputs.get("structured_output"))
                schema = config.get("json_schema")
                if not isinstance(schema, dict) or value is None:
                    raise ValidationError_("Structured Generate requires fixed output and JSON Schema")
                self._validate_json_schema(value, schema)
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id=str(config.get("schema_id", "typed_generated")), content=value if isinstance(value, dict) else {"value": value}, run_id=run.run_id))
            elif kind == "model_router":
                policy = config.get("provider_selection_policy_ref")
                models = config.get("enabled_models", [])
                if (
                    not isinstance(policy, str)
                    or not policy.startswith("atlascloud.")
                    or not isinstance(models, list)
                    or not models
                    or any(not isinstance(model, str) or not model.startswith("atlascloud/") for model in models)
                ):
                    raise ValidationError_("Model Router requires an enabled fixed AtlasCloud policy/model set")
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="provider_selection", content={"policy_ref": policy, "models": models}, run_id=run.run_id))
            elif kind == "variants":
                payloads = config.get("candidate_payloads", [])
                if not isinstance(payloads, list) or not payloads:
                    raise ValidationError_("Variants requires one or more candidate payloads")
                raw_failed = list(config.get("failed_candidates", []))
                if any(
                    not isinstance(item, dict)
                    or not (str(item.get("code", "")).strip() or str(item.get("reason", "")).strip())
                    for item in raw_failed
                ):
                    raise ValidationError_("Variants failed_candidates require a non-empty code or reason")
                # Keep both stable machine code and human explanation in the
                # immutable CandidateSet. Older callers supplied only code;
                # newer provider adapters commonly supply only reason.
                failed = [
                    {
                        **item,
                        "code": str(item.get("code") or "VARIANT_FAILED"),
                        "reason": str(item.get("reason") or item.get("code")),
                    }
                    for item in raw_failed
                ]
                costs = dict(config.get("cost_allocation", {}))
                if any(not isinstance(value, (int, float)) or value < 0 for value in costs.values()):
                    raise ValidationError_("Variants cost_allocation must contain non-negative numeric amounts")
                candidate_outputs = [self._artifact(s, owner_scope=run.owner_scope, schema_id=str(config.get("candidate_schema_id", "variant")), content=item if isinstance(item, dict) else {"value": item}, run_id=run.run_id) for item in payloads]
                refs = [{"artifact_id": str(item.artifact_id), "artifact_version_id": str(item.artifact_version_id), "schema_id": item.schema_id, "schema_version": item.schema_version} for item in candidate_outputs]
                candidate_set = CandidateSetModel(candidate_set_id=uuid4(), owner_scope=run.owner_scope, run_id=run.run_id, node_run_id=node.node_run_id, candidate_refs=refs, failed_candidates=failed, cost_allocation=costs, created_at=datetime.now(timezone.utc))
                s.add(candidate_set)
                outputs.extend(candidate_outputs)
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="candidate_set", content={"candidate_set_id": str(candidate_set.candidate_set_id), "candidate_refs": refs, "failed_candidates": failed}, run_id=run.run_id))
            elif kind == "select_rank":
                raw = config.get("candidate_set_id")
                candidate_set = s.get(CandidateSetModel, UUID(str(raw))) if raw else None
                if candidate_set is None:
                    # A sequential benchmark graph deliberately pins its run
                    # and node lineage.  It may select only the candidate set
                    # produced earlier in this same immutable run.
                    candidate_set = s.scalar(
                        select(CandidateSetModel)
                        .where(CandidateSetModel.run_id == run.run_id)
                        .order_by(CandidateSetModel.created_at.desc())
                    )
                if candidate_set is None or candidate_set.owner_scope != run.owner_scope:
                    raise ValidationError_("Select/Rank requires a fixed owner CandidateSet")
                selected = [str(value) for value in config.get("selected_version_ids", [])]
                known = {str(item["artifact_version_id"]) for item in candidate_set.candidate_refs}
                if not selected:
                    selected = [str(candidate_set.candidate_refs[0]["artifact_version_id"])] if candidate_set.candidate_refs else []
                if not selected or not set(selected).issubset(known):
                    raise ValidationError_("Select/Rank selected refs must belong to CandidateSet")
                ranking = [str(value) for value in config.get("ranking", selected)]
                record = SelectionRecordModel(selection_id=uuid4(), candidate_set_id=candidate_set.candidate_set_id, owner_scope=run.owner_scope, ranking=ranking, selected_refs=[item for item in candidate_set.candidate_refs if item["artifact_version_id"] in set(selected)], actor_or_model="workflow", rubric_revision=str(config.get("rubric_revision", "manual.v1")), rationale=str(config.get("rationale", "")), created_at=datetime.now(timezone.utc))
                s.add(record)
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="selection_record", content={"selection_id": str(record.selection_id), "selected_refs": record.selected_refs, "ranking": ranking}, run_id=run.run_id))
            elif kind == "review":
                issues = config.get("issues", [])
                if not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues):
                    raise ValidationError_("Review requires typed issue objects")
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="review_report", content={"issues": issues, "rubric_revision": str(config.get("rubric_revision", "review.v1"))}, run_id=run.run_id))
            elif kind == "transform":
                transform_id = config.get("transform_id", "identity")
                if transform_id not in {"identity", "metadata_extract"}:
                    raise ValidationError_("Transform is not a registered safe transform")
                source = config.get("source", inputs.get("source", {}))
                if not isinstance(source, dict):
                    raise ValidationError_("Transform source must be typed JSON")
                content = source if transform_id == "identity" else {"keys": sorted(source), "source": source}
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="transformed_artifact", content=content, run_id=run.run_id))
            elif kind == "package_export":
                artifact_ids = [UUID(str(value)) for value in config.get("artifact_version_ids", [])]
                if not artifact_ids:
                    # Professional templates may omit literal IDs because
                    # they do not exist at publication.  Export only consumes
                    # fixed outputs from this run, never an owner's latest
                    # artifact collection.
                    artifact_ids = [UUID(str(item["artifact_version_id"])) for item in inputs.get("upstream_artifact_refs", []) if isinstance(item, dict) and item.get("artifact_version_id")]
                if not artifact_ids:
                    raise ValidationError_("Package Export requires fixed artifact refs")
                manifest: list[dict[str, Any]] = []
                for artifact_id in artifact_ids:
                    artifact = s.get(ArtifactVersionModel, artifact_id)
                    if artifact is None or artifact.owner_scope != run.owner_scope:
                        raise ForbiddenError("Package Export has an unavailable ArtifactVersion")
                    manifest.append({"artifact_version_id": str(artifact_id), "schema_id": artifact.schema_id, "attribution": artifact.metadata_json.get("attribution", {})})
                outputs.append(self._artifact(s, owner_scope=run.owner_scope, schema_id="package_manifest", content={"items": manifest, "attribution": config.get("attribution", {})}, run_id=run.run_id))
            elif kind == "workbench_task":
                existing = s.scalar(select(HumanTaskModel).where(HumanTaskModel.node_run_id == node.node_run_id, HumanTaskModel.task_kind == "workbench_task"))
                if existing is None:
                    task = HumanTaskModel(task_id=uuid4(), task_kind="workbench_task", owner_layer="workflow", owner_revision_id=revision.revision_id, run_id=run.run_id, node_run_id=node.node_run_id, attempt_id=attempt.attempt_id, input_snapshot_refs=list(config.get("input_snapshot_refs", [])), policy_strength="domain_required", schema_ref=str(config.get("output_schema_ref", "workbench_result.v1")), timeout_policy={"target_workbench": str(config.get("target_workbench", "generic")), "resource_type": str(config.get("resource_type", "generic")), "expected_draft_version": int(config.get("expected_draft_version", 0))}, status=HumanTaskStatus.PENDING, task_version=1, created_at=datetime.now(timezone.utc))
                    s.add(task)
                attempt.status, node.status, run.status = AttemptStatus.WAITING_EXTERNAL, NodeRunStatus.WAITING_USER, RunStatus.WAITING_USER
                s.flush()
                return []
            else:
                raise ValidationError_(f"Unsupported public business node {kind}")
            attempt.status, attempt.completed_at = AttemptStatus.COMPLETED, datetime.now(timezone.utc)
            node.status = NodeRunStatus.COMPLETED
            # Completion is also the scheduling boundary.  The scheduler
            # materialises downstream attempts from this fixed revision only.
            from src.domain.runtime.runtime_service import RuntimeService
            RuntimeService(session_factory=self._factory)._sql_schedule_ready(s, run)
            s.flush()
            return outputs

    def create_workbench_task(
        self, *, owner_scope: str, workflow_revision_id: UUID, run_id: UUID, node_run_id: UUID, attempt_id: UUID,
        input_snapshot_refs: list[dict[str, Any]], target_workbench: str, output_schema_ref: str,
        resource_type: str, expected_draft_version: int = 0,
    ) -> HumanTaskModel:
        if not target_workbench or not output_schema_ref or not resource_type or expected_draft_version < 0:
            raise ValidationError_("WorkbenchTask requires target, output schema, resource type and non-negative draft version")
        with self._factory.begin() as s:
            revision = s.get(WorkflowRevisionModel, workflow_revision_id)
            run = s.get(WorkflowRunModel, run_id)
            node = s.get(NodeRunModel, node_run_id)
            attempt = s.get(NodeRunAttemptModel, attempt_id)
            if not revision or not run or not node or not attempt or run.workflow_revision_id != workflow_revision_id or node.run_id != run_id or attempt.node_run_id != node_run_id:
                raise ValidationError_("WorkbenchTask must be owned by a fixed WorkflowRevision/run/node/attempt")
            if run.owner_scope != owner_scope:
                raise ForbiddenError("Only the workflow owner may create WorkbenchTask")
            if node.node_type_id != "workbench_task":
                raise ValidationError_("Only a workflow workbench_task node can materialize WorkbenchTask")
            existing = s.scalar(select(HumanTaskModel).where(HumanTaskModel.node_run_id == node_run_id, HumanTaskModel.task_kind == "workbench_task"))
            if existing is not None:
                return existing
            task = HumanTaskModel(task_id=uuid4(), task_kind="workbench_task", owner_layer="workflow", owner_revision_id=workflow_revision_id,
                run_id=run_id, node_run_id=node_run_id, attempt_id=attempt_id, input_snapshot_refs=input_snapshot_refs,
                policy_strength="domain_required", schema_ref=output_schema_ref,
                timeout_policy={"target_workbench": target_workbench, "resource_type": resource_type, "expected_draft_version": expected_draft_version},
                status=HumanTaskStatus.PENDING, task_version=1, created_at=datetime.now(timezone.utc))
            s.add(task)
            attempt.status = AttemptStatus.WAITING_EXTERNAL
            node.status = NodeRunStatus.WAITING_USER
            run.status = RunStatus.WAITING_USER
            s.flush()
            return task

    def submit_workbench_task(
        self, *, task_id: UUID, owner_scope: str, actor_id: UUID, task_version: int,
        idempotency_token: str, output_artifact_version_ids: list[UUID], resource_id: UUID | None = None,
    ) -> list[ResourceCommitModel]:
        if not idempotency_token or not output_artifact_version_ids:
            raise ValidationError_("WorkbenchTask submit requires idempotency token and typed outputs")
        if len(output_artifact_version_ids) != 1:
            raise ValidationError_("WorkbenchTask ResourceCommit currently requires exactly one typed output")
        with self._factory.begin() as s:
            task = s.get(HumanTaskModel, task_id)
            if task is None or task.task_kind != "workbench_task" or task.owner_layer != "workflow":
                raise NotFoundError("WorkbenchTask", str(task_id))
            run = s.get(WorkflowRunModel, task.run_id)
            if run is None or run.owner_scope != owner_scope:
                raise ForbiddenError("Only the workflow owner may submit WorkbenchTask")
            prior = s.scalar(select(HumanTaskDecisionModel).where(HumanTaskDecisionModel.task_id == task_id, HumanTaskDecisionModel.idempotency_token == idempotency_token))
            if prior is not None:
                return list(s.scalars(select(ResourceCommitModel).where(ResourceCommitModel.task_id == task_id)))
            if task.status != HumanTaskStatus.PENDING or task.task_version != task_version:
                raise ConflictError("WorkbenchTask version is stale or already closed")
            policy = dict(task.timeout_policy or {})
            resource = resource_id or uuid4()
            resource_row = s.get(ResourceModel, resource)
            if resource_row is None:
                if resource_id is not None or int(policy["expected_draft_version"]) != 0:
                    raise ConflictError("ResourceDraft CAS conflict")
                resource_row = ResourceModel(resource_id=resource, resource_type=str(policy["resource_type"]), owner_scope=owner_scope, created_at=datetime.now(timezone.utc))
                s.add(resource_row)
                s.flush()
                draft = ResourceDraftModel(resource_id=resource, draft_version=0, base_revision_id=None,
                    content_artifact_version_id=output_artifact_version_ids[0], updated_at=datetime.now(timezone.utc))
                s.add(draft)
                s.flush()
            elif resource_row.owner_scope != owner_scope or resource_row.resource_type != str(policy["resource_type"]):
                raise ForbiddenError("WorkbenchTask ResourceCommit is not owned by this workflow")
            draft = s.get(ResourceDraftModel, resource)
            if draft is None or draft.draft_version != int(policy["expected_draft_version"]):
                raise ConflictError("ResourceDraft CAS conflict")
            previous = s.get(ResourceRevisionModel, draft.base_revision_id) if draft.base_revision_id else None
            next_number = (previous.revision_number if previous is not None else 0) + 1
            commits: list[ResourceCommitModel] = []
            for index, artifact_id in enumerate(output_artifact_version_ids):
                artifact = s.get(ArtifactVersionModel, artifact_id)
                if artifact is None or artifact.owner_scope != owner_scope or f"{artifact.schema_id}.v{artifact.schema_version}" != task.schema_ref:
                    raise ValidationError_("WorkbenchTask output does not satisfy its fixed output schema")
                revision_id = uuid4()
                if previous is not None:
                    previous.revision_status = "retired"
                s.add(ResourceRevisionModel(revision_id=revision_id, resource_id=resource, revision_number=next_number + index,
                    content_artifact_version_id=artifact_id, revision_status="active", created_from_artifact_version_id=previous.content_artifact_version_id if previous else None,
                    created_at=datetime.now(timezone.utc)))
                commit = ResourceCommitModel(commit_id=uuid4(), task_id=task_id, resource_id=resource, revision_id=revision_id,
                    revision_number=next_number + index, resource_type=str(policy["resource_type"]), owner_scope=owner_scope,
                    source_artifact_version_id=artifact_id, expected_draft_version=int(policy["expected_draft_version"]), committed_at=datetime.now(timezone.utc))
                commits.append(commit)
                s.add(commit)
            draft.base_revision_id = commits[-1].revision_id
            draft.content_artifact_version_id = output_artifact_version_ids[-1]
            draft.draft_version += 1
            s.add(HumanTaskDecisionModel(decision_id=uuid4(), task_id=task_id, task_version=task_version, action="accept", actor_id=actor_id,
                actor_scope=owner_scope, typed_payload={"output_artifact_version_ids": [str(value) for value in output_artifact_version_ids]},
                notes="", policy_evidence_refs=[], idempotency_token=idempotency_token, created_at=datetime.now(timezone.utc)))
            task.status = HumanTaskStatus.ACCEPTED
            node = s.get(NodeRunModel, task.node_run_id)
            attempt = s.get(NodeRunAttemptModel, task.attempt_id)
            if node is not None:
                node.status = NodeRunStatus.COMPLETED
            if attempt is not None:
                fixed_input = dict(attempt.fixed_input or {})
                fixed_input["committed_resource_refs"] = [
                    {
                        "resource_id": str(commit.resource_id),
                        "resource_type": commit.resource_type,
                        "revision_id": str(commit.revision_id),
                    }
                    for commit in commits
                ]
                attempt.fixed_input = fixed_input
                attempt.status = AttemptStatus.COMPLETED
            if run.status == RunStatus.WAITING_USER:
                run.status = RunStatus.RUNNING
            # ResourceRefs become visible only after the CAS/revision/commit
            # writes above are in this transaction.  Then schedule downstream
            # nodes against the same durable run snapshot.
            s.flush()
            from src.domain.runtime.runtime_service import RuntimeService

            RuntimeService(session_factory=self._factory)._sql_schedule_ready(s, run)
            s.flush()
            return commits
