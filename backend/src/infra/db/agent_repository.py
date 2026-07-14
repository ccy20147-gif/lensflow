"""TF-ASR-001: PostgreSQL-backed Agent repository.

Draft/Revision lifecycle with CAS base_hash enforcement.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.infra.db.models import (
    AgentDefinitionModel,
    AgentDraftModel,
    AgentTrialRunModel,
    AgentTrialStepTraceModel,
    AgentTrialRequestInputModel,
    AgentRevisionModel,
    ArtifactVersionModel,
    ResourceDraftModel,
    ResourceModel,
    ResourceRevisionModel,
    SkillContentModel,
    SkillRevisionModel,
    ToolDefinitionModel,
    ToolRevisionModel,
    WorkflowRevisionModel,
    NodeRunAttemptModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import AgentRevision, ResourceRef


def _compute_hash(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _revision_row_to_schema(row: AgentRevisionModel, agent_kind: str = "") -> AgentRevision:
    body = dict(row.body or {})
    body["revision_id"] = str(row.revision_id)
    body["agent_kind"] = agent_kind
    body["revision_number"] = row.revision_number
    body["content_hash"] = row.content_hash
    body["revision_status"] = row.status
    return AgentRevision.model_validate(body)


class SqlAgentRepository:
    """Persistent Agent Definition + Revision storage with CAS."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # -- Definitions --

    def create_definition(
        self, *, name: str, description: str, agent_kind: str, owner_scope: str,
        cloned_from_agent_id: UUID | None = None,
    ) -> AgentDefinitionModel:
        if not name:
            raise ValidationError_(message="Agent definition requires a name")
        with self._factory.begin() as session:
            row = AgentDefinitionModel(
                agent_id=uuid4(),
                name=name,
                description=description,
                agent_kind=agent_kind,
                owner_scope=owner_scope,
                cloned_from_agent_id=cloned_from_agent_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(row)
            # Agent identity is a first-class Resource identity.  Keeping the
            # same UUID avoids a parallel namespace and lets workflow/package
            # code retain one fixed ResourceRef contract.
            session.add(ResourceModel(
                resource_id=row.agent_id, resource_type="agent",
                owner_scope=owner_scope, created_at=datetime.now(timezone.utc),
            ))
            session.add(AgentDraftModel(
                agent_id=row.agent_id, draft_version=1, base_revision_id=None,
                body={}, content_hash=_compute_hash({}), updated_at=datetime.now(timezone.utc),
            ))
            session.flush()
            return row

    def get_definition(self, agent_id: UUID) -> AgentDefinitionModel:
        with self._factory() as session:
            row = session.get(AgentDefinitionModel, agent_id)
            if row is None:
                raise NotFoundError("AgentDefinition", str(agent_id))
            return row

    def list_definitions(self, *, owner_scope: str | None = None) -> list[AgentDefinitionModel]:
        stmt = select(AgentDefinitionModel).order_by(AgentDefinitionModel.created_at.desc())
        if owner_scope is not None:
            stmt = stmt.where(AgentDefinitionModel.owner_scope == owner_scope)
        with self._factory() as session:
            return list(session.scalars(stmt))

    def update_definition(
        self, agent_id: UUID, *, name: str | None = None, description: str | None = None
    ) -> AgentDefinitionModel:
        with self._factory.begin() as session:
            row = session.get(AgentDefinitionModel, agent_id)
            if row is None:
                raise NotFoundError("AgentDefinition", str(agent_id))
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def delete_definition(self, agent_id: UUID) -> None:
        with self._factory.begin() as session:
            row = session.get(AgentDefinitionModel, agent_id)
            if row is None:
                raise NotFoundError("AgentDefinition", str(agent_id))
            # Also cascade-delete revisions
            session.query(AgentRevisionModel).filter(
                AgentRevisionModel.agent_id == agent_id
            ).delete()
            session.query(AgentDraftModel).filter(
                AgentDraftModel.agent_id == agent_id
            ).delete()
            session.delete(row)
            session.flush()

    def get_draft(self, agent_id: UUID) -> AgentDraftModel:
        with self._factory() as session:
            row = session.get(AgentDraftModel, agent_id)
            if row is None:
                raise NotFoundError("AgentDraft", str(agent_id))
            return row

    def save_draft(self, agent_id: UUID, *, body: dict, base_draft_version: int) -> AgentDraftModel:
        """CAS-save mutable authoring state without creating a revision."""
        from src.domain.agent.agent_service import validate_agent
        validate_agent(body)
        with self._factory.begin() as session:
            definition = session.get(AgentDefinitionModel, agent_id)
            draft = session.get(AgentDraftModel, agent_id, with_for_update=True)
            if definition is None or draft is None:
                raise NotFoundError("AgentDraft", str(agent_id))
            if draft.draft_version != base_draft_version:
                raise ConflictError("AgentDraft compare-and-swap conflict")
            draft.body = dict(body)
            draft.content_hash = _compute_hash(body)
            draft.draft_version += 1
            draft.updated_at = datetime.now(timezone.utc)
            session.flush()
            return draft

    def submit_draft(self, agent_id: UUID, *, base_draft_version: int) -> AgentRevision:
        """Submit the already-saved draft body; callers cannot replace it."""
        with self._factory.begin() as session:
            draft = session.get(AgentDraftModel, agent_id, with_for_update=True)
            if draft is None:
                raise NotFoundError("AgentDraft", str(agent_id))
            if draft.draft_version != base_draft_version:
                raise ConflictError("AgentDraft compare-and-swap conflict")
            body = dict(draft.body or {})
            # Reserve this authoring version before opening the independent
            # immutable-revision transaction below. A concurrent submit/save
            # necessarily observes a different draft_version.
            draft.draft_version += 1
            draft.updated_at = datetime.now(timezone.utc)
        try:
            return self.create_revision(agent_id, body)
        except Exception:
            # Release an unused reservation only if no later author changed
            # the draft. This preserves CAS semantics on a failed validation.
            with self._factory.begin() as session:
                draft = session.get(AgentDraftModel, agent_id, with_for_update=True)
                if draft is not None and draft.draft_version == base_draft_version + 1:
                    draft.draft_version = base_draft_version
            raise

    def dry_run_draft(self, agent_id: UUID, *, draft_version: int, budget: dict,
                      fixed_input: dict | None = None, simulated_output: dict | None = None,
                      usage: dict | None = None, tool_disclosures: list[dict] | None = None) -> dict:
        """Run an isolated fixed sample through Agent compile/schema contracts.

        `simulated_output` is an injectable Atlas fixture for Studio/tests; it
        never opens a network connection or creates a business Artifact.
        """
        from src.domain.agent.agent_compiler import compile_agent
        with self._factory.begin() as session:
            definition = session.get(AgentDefinitionModel, agent_id)
            draft = session.get(AgentDraftModel, agent_id)
            if definition is None or draft is None:
                raise NotFoundError("AgentDraft", str(agent_id))
            if draft.draft_version != draft_version:
                raise ConflictError("AgentDraft compare-and-swap conflict")
            plan = compile_agent(dict(draft.body or {}))
            max_cost = budget.get("max_cost")
            declared_cost = (draft.body or {}).get("execution_policy", {}).get("max_cost", 0)
            if isinstance(max_cost, (int, float)) and isinstance(declared_cost, (int, float)) and declared_cost > max_cost:
                raise ValidationError_("Dry-run budget would be exceeded")
            output_schema = (draft.body or {}).get("output_schema")
            status, failure_owner = "completed", None
            if simulated_output is not None and output_schema is not None:
                try:
                    from src.domain.agent.schema_validation import validate_json_schema
                    validate_json_schema(simulated_output, output_schema)
                except ValidationError_:
                    status, failure_owner = "failed", "output_schema"
            trial = AgentTrialRunModel(trial_id=uuid4(), agent_id=agent_id, owner_scope=definition.owner_scope,
                draft_version=draft_version, fixed_body=dict(draft.body or {}), fixed_input=dict(fixed_input or {}), budget=dict(budget),
                status=status, failure_owner=failure_owner, created_at=datetime.now(timezone.utc))
            session.add(trial)
            for step in plan["compiled_steps"]:
                session.add(AgentTrialStepTraceModel(trace_id=uuid4(), trial_id=trial.trial_id,
                    step_id=step["step_id"], status="failed" if failure_owner else "completed",
                    usage=dict(usage or {"estimated_tokens": 0}), tool_disclosures=list(tool_disclosures or []),
                    failure_owner=failure_owner, created_at=datetime.now(timezone.utc)))
            session.flush()
            return {"valid": not failure_owner, "plan_hash": plan["plan_hash"], "trial_id": str(trial.trial_id), "budget": dict(budget), "status": trial.status, "failure_owner": failure_owner}

    def clone_definition(self, agent_id: UUID, *, owner_scope: str, name: str) -> AgentDefinitionModel:
        """Clone immutable authoring content, never credential bindings."""
        source = self.get_definition(agent_id)
        revisions = self.list_revisions(agent_id)
        active = next((item for item in revisions if item.revision_status.value == "active"), None)
        clone = self.create_definition(name=name, description=source.description, agent_kind="configurable",
            owner_scope=owner_scope, cloned_from_agent_id=agent_id)
        if active is not None:
            body = active.model_dump(mode="json", exclude={"revision_id", "agent_kind", "revision_number", "content_hash", "revision_status"})
            # Credentials are runtime-only typed inputs. Mark Tool revisions as
            # requiring a fresh binding rather than copying any binding ID.
            body.pop("credential_binding", None)
            body["clone_lineage"] = {"source_agent_id": str(agent_id), "source_revision_id": str(active.revision_id)}
            self.save_draft(clone.agent_id, body=body, base_draft_version=1)
        return clone

    def create_trial_request_input(self, trial_id: UUID, *, schema_ref: str, question: str, input_schema: dict) -> AgentTrialRequestInputModel:
        with self._factory.begin() as session:
            trial = session.get(AgentTrialRunModel, trial_id)
            if trial is None:
                raise NotFoundError("AgentTrialRun", str(trial_id))
            row = AgentTrialRequestInputModel(task_id=uuid4(), trial_id=trial_id, schema_ref=schema_ref,
                question=question, input_schema=input_schema, status="waiting", task_version=1, created_at=datetime.now(timezone.utc))
            trial.status = "waiting_user"
            session.add(row)
            return row

    def resolve_trial_request_input(self, task_id: UUID, *, task_version: int, answer: dict) -> AgentTrialRequestInputModel:
        from src.domain.agent.request_input import _validate_typed_answer
        with self._factory.begin() as session:
            row = session.get(AgentTrialRequestInputModel, task_id, with_for_update=True)
            if row is None:
                raise NotFoundError("AgentTrialRequestInput", str(task_id))
            if row.task_version != task_version or row.status != "waiting":
                raise ConflictError("Trial RequestInput is stale or already resolved")
            _validate_typed_answer(answer, dict(row.input_schema or {}), int((row.input_schema or {}).get("max_response_bytes", 16384)))
            row.answer, row.status, row.task_version = answer, "accepted", row.task_version + 1
            trial = session.get(AgentTrialRunModel, row.trial_id)
            if trial is not None:
                trial.status = "completed"
            return row

    def get_trial_request_input(self, task_id: UUID, *, owner_scope: str) -> AgentTrialRequestInputModel:
        with self._factory() as session:
            row = session.get(AgentTrialRequestInputModel, task_id)
            trial = session.get(AgentTrialRunModel, row.trial_id) if row else None
            if row is None or trial is None:
                raise NotFoundError("AgentTrialRequestInput", str(task_id))
            if trial.owner_scope != owner_scope:
                raise ForbiddenError("Trial RequestInput belongs to a different owner")
            return row

    def list_trial_request_inputs(self, trial_id: UUID, *, owner_scope: str) -> list[AgentTrialRequestInputModel]:
        with self._factory() as session:
            trial = session.get(AgentTrialRunModel, trial_id)
            if trial is None:
                raise NotFoundError("AgentTrialRun", str(trial_id))
            if trial.owner_scope != owner_scope:
                raise ForbiddenError("Agent trial belongs to a different owner")
            return list(session.scalars(select(AgentTrialRequestInputModel).where(
                AgentTrialRequestInputModel.trial_id == trial_id
            ).order_by(AgentTrialRequestInputModel.created_at.desc())))

    # -- Revisions (Draft / Revision lifecycle) --

    def create_revision(
        self, agent_id: UUID, body: dict, *, base_hash: str | None = None
    ) -> AgentRevision:
        """Create a new draft revision with CAS base_hash check."""
        content_hash = _compute_hash(body)
        # Determine next revision number
        with self._factory.begin() as session:
            # Fetch agent definition for agent_kind
            def_row = session.get(AgentDefinitionModel, agent_id)
            if def_row is None:
                raise NotFoundError("AgentDefinition", str(agent_id))
            agent_kind = def_row.agent_kind
            self._validate_dependencies(session, def_row, body)

            latest = session.scalar(
                select(AgentRevisionModel)
                .where(AgentRevisionModel.agent_id == agent_id)
                .order_by(AgentRevisionModel.revision_number.desc())
                .limit(1)
            )
            next_number = (latest.revision_number + 1) if latest else 1

            # If base_hash provided, verify it matches the latest revision
            if base_hash is not None:
                if latest is None:
                    raise ConflictError(
                        message="No existing revision to base on for CAS check"
                    )
                if latest.content_hash != base_hash:
                    raise ConflictError(
                        message="CAS conflict: base_hash does not match latest revision content_hash"
                    )

            row = AgentRevisionModel(
                revision_id=uuid4(),
                agent_id=agent_id,
                revision_number=next_number,
                body=body,
                content_hash=content_hash,
                base_hash=base_hash,
                status="draft",
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            # The exact revision body is also an immutable ArtifactVersion;
            # ResourceDraft points at the latest authoring body while every
            # AgentRevision has its own frozen ResourceRevision row.
            artifact = ArtifactVersionModel(
                artifact_version_id=uuid4(), artifact_id=agent_id,
                schema_id="toonflow.agent_revision", schema_version=1,
                owner_scope=def_row.owner_scope, content_json=dict(body),
                content_hash=content_hash,
                metadata_json={"agent_revision_id": str(row.revision_id)},
                created_at=datetime.now(timezone.utc),
            )
            session.add(artifact)
            resource = session.get(ResourceModel, agent_id)
            if resource is None:
                # Upgrade path for pre-Resource Agent definitions.
                resource = ResourceModel(resource_id=agent_id, resource_type="agent", owner_scope=def_row.owner_scope,
                                         created_at=datetime.now(timezone.utc))
                session.add(resource)
                session.flush()
            draft = session.get(ResourceDraftModel, agent_id)
            if draft is None:
                draft = ResourceDraftModel(resource_id=agent_id, draft_version=1, base_revision_id=row.revision_id,
                    content_artifact_version_id=artifact.artifact_version_id, updated_at=datetime.now(timezone.utc))
                session.add(draft)
            else:
                draft.draft_version += 1
                draft.base_revision_id = row.revision_id
                draft.content_artifact_version_id = artifact.artifact_version_id
                draft.updated_at = datetime.now(timezone.utc)
            session.add(ResourceRevisionModel(
                revision_id=row.revision_id, resource_id=agent_id, revision_number=next_number,
                content_artifact_version_id=artifact.artifact_version_id,
                revision_status=RevisionStatus.DRAFT, created_at=datetime.now(timezone.utc),
            ))
            author_draft = session.get(AgentDraftModel, agent_id)
            if author_draft is None:
                author_draft = AgentDraftModel(agent_id=agent_id, draft_version=1, base_revision_id=None,
                    body={}, content_hash=_compute_hash({}), updated_at=datetime.now(timezone.utc))
                session.add(author_draft)
            author_draft.body = dict(body)
            author_draft.content_hash = content_hash
            author_draft.base_revision_id = row.revision_id
            author_draft.draft_version += 1
            author_draft.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _revision_row_to_schema(row, agent_kind=agent_kind)

    def get_revision(self, revision_id: UUID) -> AgentRevision:
        with self._factory() as session:
            row = session.get(AgentRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("AgentRevision", str(revision_id))
            # Fetch agent_kind from parent definition
            def_row = session.get(AgentDefinitionModel, row.agent_id)
            agent_kind = def_row.agent_kind if def_row else "configurable"
            return _revision_row_to_schema(row, agent_kind=agent_kind)

    def get_definition_for_revision(self, revision_id: UUID) -> AgentDefinitionModel:
        """Resolve a revision's trusted owner definition."""
        with self._factory() as session:
            revision = session.get(AgentRevisionModel, revision_id)
            if revision is None:
                raise NotFoundError("AgentRevision", str(revision_id))
            definition = session.get(AgentDefinitionModel, revision.agent_id)
            if definition is None:
                raise NotFoundError("AgentDefinition", str(revision.agent_id))
            return definition

    @staticmethod
    def _validate_dependencies(
        session: Session, definition: AgentDefinitionModel, body: dict
    ) -> None:
        """Pin active dependencies and verify cross-owner Skill grants now.

        The immutable body retains a full ResourceRef for foreign Skills.  A
        UUID alone is intentionally valid only for the definition owner.
        Invocation repeats this grant check, so creation-time success never
        turns a historical snapshot into standing authority.
        """
        for index, raw in enumerate(body.get("skill_revision_refs", [])):
            ref: ResourceRef | None = None
            try:
                if isinstance(raw, ResourceRef):
                    ref = raw
                elif isinstance(raw, dict):
                    ref = ResourceRef.model_validate(raw)
                skill_id = ref.revision_id if ref is not None else UUID(str(raw))
            except (TypeError, ValueError) as exc:
                raise ValidationError_(
                    "Skill dependency must be a frozen UUID or ResourceRef",
                    details={"field": f"skill_revision_refs[{index}]"},
                ) from exc
            skill_revision = session.get(SkillRevisionModel, skill_id)
            if skill_revision is None or skill_revision.status != "active":
                raise ValidationError_(
                    "Agent dependency references an inactive or unknown SkillRevision",
                    details={"field": f"skill_revision_refs[{index}]"},
                )
            skill = session.get(SkillContentModel, skill_revision.skill_id)
            if skill is None:
                raise ValidationError_("Agent SkillRevision parent does not exist", details={"field": f"skill_revision_refs[{index}]"})
            if skill.owner_scope != definition.owner_scope:
                if ref is None or ref.resource_id != skill.skill_id or ref.resource_type != "skill":
                    raise ValidationError_(
                        "Cross-owner SkillRevision requires a fixed Skill ResourceRef",
                        details={"field": f"skill_revision_refs[{index}]"},
                    )
                # This method already owns the current SQL transaction; use
                # the rows directly so a concurrent revoke is serialized by
                # the database check repeated at execution time.
                from src.infra.db.models import ResourceGrantSnapshotModel, ResourceModel, ResourceRevisionModel
                resource = session.get(ResourceModel, ref.resource_id)
                resource_revision = session.get(ResourceRevisionModel, ref.revision_id)
                grant = session.get(ResourceGrantSnapshotModel, ref.grant_snapshot_id) if ref.grant_snapshot_id else None
                if (
                    resource is None or resource_revision is None
                    or resource_revision.resource_id != ref.resource_id
                    or resource.owner_scope != skill.owner_scope
                    or resource.resource_type != "skill"
                    or resource_revision.revision_status != RevisionStatus.ACTIVE
                    or grant is None or grant.resource_revision_id != ref.revision_id
                    or grant.grantee_scope != definition.owner_scope or grant.status != "active"
                    or not {"reference", "execute"}.issubset(set(grant.capability_actions or []))
                ):
                    raise ForbiddenError("Skill ResourceRef grant is unavailable, revoked, or lacks reference/execute")
        for index, raw_id in enumerate(body.get("tool_revision_refs", [])):
            try:
                tool_revision_id = UUID(str(raw_id))
            except (TypeError, ValueError) as exc:
                raise ValidationError_(
                    "Tool dependency must be a UUID",
                    details={"field": f"tool_revision_refs[{index}]"},
                ) from exc
            revision = session.get(ToolRevisionModel, tool_revision_id)
            if (
                revision is None
                or revision.status != "active"
                or revision.approval_status != "approved"
            ):
                raise ValidationError_(
                    "Agent dependency references an unavailable ToolRevision",
                    details={"field": f"tool_revision_refs[{index}]"},
                )
            tool = session.get(ToolDefinitionModel, revision.tool_id)
            if tool is None or tool.owner_scope != definition.owner_scope:
                raise ValidationError_(
                    "Agent ToolRevision must belong to the Agent owner_scope",
                    details={"field": f"tool_revision_refs[{index}]"},
                )
        plan_by_revision = {
            str(entry.get("tool_revision_id")): entry
            for entry in body.get("tool_access_plan", [])
            if isinstance(entry, dict)
        }
        for raw_id in body.get("tool_revision_refs", []):
            revision_id = UUID(str(raw_id))
            revision = session.get(ToolRevisionModel, revision_id)
            assert revision is not None
            plan = plan_by_revision.get(str(revision_id))
            if plan is None:
                raise ValidationError_("ToolRevision lacks a frozen access plan", details={"field": "tool_access_plan"})
            available_operations = {
                str(item.get("id")): item
                for item in (revision.body or {}).get("operations", [])
                if isinstance(item, dict)
            }
            for operation in plan.get("operations", []):
                operation_id = str(operation.get("operation_id", ""))
                registered = available_operations.get(operation_id)
                if registered is None:
                    raise ValidationError_("Tool access plan references an unknown operation", details={"field": "tool_access_plan"})
                allowed_fields = set(registered.get("disclosure_fields", []))
                if not set(operation.get("disclosure_fields", [])).issubset(allowed_fields):
                    raise ValidationError_("Tool access plan discloses fields not approved by ToolRevision", details={"field": "tool_access_plan"})

    def ensure_revision_belongs_to(self, agent_id: UUID, revision_id: UUID) -> None:
        with self._factory() as session:
            row = session.get(AgentRevisionModel, revision_id)
            if row is None or row.agent_id != agent_id:
                raise NotFoundError("AgentRevision", str(revision_id))

    def list_revisions(self, agent_id: UUID) -> list[AgentRevision]:
        stmt = (
            select(AgentRevisionModel)
            .where(AgentRevisionModel.agent_id == agent_id)
            .order_by(AgentRevisionModel.revision_number.desc())
        )
        with self._factory() as session:
            def_row = session.get(AgentDefinitionModel, agent_id)
            agent_kind = def_row.agent_kind if def_row else "configurable"
            return [_revision_row_to_schema(r, agent_kind=agent_kind) for r in session.scalars(stmt)]

    def diff_revisions(self, left_id: UUID, right_id: UUID) -> dict:
        """Field-level immutable revision diff, suitable for explicit upgrades."""
        left = self.get_revision(left_id).model_dump(mode="json")
        right = self.get_revision(right_id).model_dump(mode="json")
        ignored = {"revision_id", "revision_number", "content_hash", "revision_status"}
        changed = {
            key: {"before": left.get(key), "after": right.get(key)}
            for key in sorted((set(left) | set(right)) - ignored)
            if left.get(key) != right.get(key)
        }
        return {"from_revision_id": str(left_id), "to_revision_id": str(right_id), "changed_fields": changed}

    def usage_index(self, revision_id: UUID) -> dict[str, list[str]]:
        """Return immutable workflow and runtime references to one revision."""
        with self._factory() as session:
            if session.get(AgentRevisionModel, revision_id) is None:
                raise NotFoundError("AgentRevision", str(revision_id))
            workflows: list[str] = []
            for workflow_revision in session.scalars(select(WorkflowRevisionModel)):
                nodes = (workflow_revision.graph or {}).get("nodes", [])
                if any(
                    isinstance(node, dict)
                    and str((node.get("config") or node.get("data") or {}).get("agent_revision_id", "")) == str(revision_id)
                    for node in nodes
                ):
                    workflows.append(str(workflow_revision.revision_id))
            attempts = [
                str(row.attempt_id)
                for row in session.scalars(select(NodeRunAttemptModel))
                if str((row.fixed_input or {}).get("agent_revision_id", "")) == str(revision_id)
            ]
            return {"workflow_revision_ids": sorted(workflows), "attempt_ids": sorted(attempts)}

    def promote_revision(self, revision_id: UUID) -> AgentRevision:
        """Promote a draft revision to active (immutable)."""
        with self._factory.begin() as session:
            row = session.get(AgentRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("AgentRevision", str(revision_id))
            if row.status == "active":
                return _revision_row_to_schema(row)
            if row.status not in {"draft", "retired"}:
                raise ConflictError(
                    message=f"Cannot promote revision with status {row.status}"
                )
            row.status = "active"
            resource_revision = session.get(ResourceRevisionModel, revision_id)
            if resource_revision is not None:
                resource_revision.revision_status = RevisionStatus.ACTIVE
            # Activation is explicit.  Existing active revisions remain
            # callable because workflows always pin an exact revision.
            session.flush()
            return _revision_row_to_schema(row)

    def retire_revision(self, revision_id: UUID) -> AgentRevision:
        with self._factory.begin() as session:
            row = session.get(AgentRevisionModel, revision_id)
            if row is None:
                raise NotFoundError("AgentRevision", str(revision_id))
            row.status = "retired"
            resource_revision = session.get(ResourceRevisionModel, revision_id)
            if resource_revision is not None:
                resource_revision.revision_status = RevisionStatus.RETIRED
            session.flush()
            return _revision_row_to_schema(row)


class SqlAgentService:
    """Higher-level Agent service combining definitions + revisions."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._repo = SqlAgentRepository(factory)
        self._factory = factory or get_session_factory()

    def validate(self, body: dict) -> None:
        from src.domain.agent.agent_service import validate_agent
        validate_agent(body)

    def prepare(self, body: dict) -> dict:
        """Prepare body for a new revision (validate + enrich)."""
        self.validate(body)
        # Ensure required keys exist
        prepared = dict(body)
        prepared.setdefault("execution_policy", {})
        prepared.setdefault("sop_steps", body.get("steps", []))
        return prepared

    def dry_run(self, body: dict) -> dict:
        """Validate without persisting anything."""
        from src.domain.agent.agent_compiler import compile_agent
        return compile_agent(body)
