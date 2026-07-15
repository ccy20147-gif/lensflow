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
    OutboxEventModel,
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
        """Freeze the current draft in one transaction.

        The draft lock is deliberately held while validation and every
        immutable write complete.  In particular, never reserve a draft
        version in one transaction and create a revision in another: a failed
        revision write must leave no observable authoring-state change.
        """
        from src.domain.agent.agent_service import validate_agent
        with self._factory.begin() as session:
            definition = session.get(AgentDefinitionModel, agent_id)
            draft = session.get(AgentDraftModel, agent_id, with_for_update=True)
            if definition is None or draft is None:
                raise NotFoundError("AgentDraft", str(agent_id))
            if draft.draft_version != base_draft_version:
                raise ConflictError(
                    "AgentDraft compare-and-swap conflict",
                    details=self._draft_conflict_details(draft),
                )
            body = dict(draft.body or {})
            validate_agent(body)
            revision = self._write_revision_in_session(
                session, definition=definition, draft=draft, body=body,
            )
            # A successful submit consumes exactly the submitted authoring
            # version. All attached resource/artifact/revision rows are
            # flushed before the surrounding context commits.
            draft.draft_version += 1
            draft.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _revision_row_to_schema(revision, agent_kind=definition.agent_kind)

    @staticmethod
    def _draft_conflict_details(draft: AgentDraftModel) -> dict:
        return {
            "current_draft_version": draft.draft_version,
            "current_content_hash": draft.content_hash,
            "base_revision_id": str(draft.base_revision_id) if draft.base_revision_id else None,
        }

    def _write_revision_in_session(
        self,
        session: Session,
        *,
        definition: AgentDefinitionModel,
        draft: AgentDraftModel,
        body: dict,
        base_hash: str | None = None,
    ) -> AgentRevisionModel:
        """Write an Agent revision and canonical resource projection.

        The caller owns the transaction and, for authoring submit, the locked
        AgentDraft row. This small seam is intentionally testable so a forced
        failure proves that no partial reservation or immutable row commits.
        """
        content_hash = _compute_hash(body)
        latest = session.scalar(
            select(AgentRevisionModel)
            .where(AgentRevisionModel.agent_id == definition.agent_id)
            .order_by(AgentRevisionModel.revision_number.desc())
            .limit(1)
        )
        next_number = (latest.revision_number + 1) if latest else 1
        if definition.agent_kind == "managed_preset" and latest is not None:
            raise ForbiddenError("Managed preset Agent revisions are platform-locked")
        self._validate_dependencies(session, definition, body)
        if base_hash is not None:
            if latest is None:
                raise ConflictError(message="No existing revision to base on for CAS check")
            if latest.content_hash != base_hash:
                raise ConflictError(message="CAS conflict: base_hash does not match latest revision content_hash")

        row = AgentRevisionModel(
            revision_id=uuid4(), agent_id=definition.agent_id,
            revision_number=next_number, body=dict(body), content_hash=content_hash,
            base_hash=base_hash, status="draft", created_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        artifact = ArtifactVersionModel(
            artifact_version_id=uuid4(), artifact_id=definition.agent_id,
            schema_id="toonflow.agent_revision", schema_version=1,
            owner_scope=definition.owner_scope, content_json=dict(body),
            content_hash=content_hash,
            metadata_json={"agent_revision_id": str(row.revision_id)},
            created_at=datetime.now(timezone.utc),
        )
        session.add(artifact)
        resource = session.get(ResourceModel, definition.agent_id)
        if resource is None:
            resource = ResourceModel(resource_id=definition.agent_id, resource_type="agent",
                                     owner_scope=definition.owner_scope, created_at=datetime.now(timezone.utc))
            session.add(resource)
            session.flush()
        resource_draft = session.get(ResourceDraftModel, definition.agent_id)
        if resource_draft is None:
            session.add(ResourceDraftModel(
                resource_id=definition.agent_id, draft_version=1, base_revision_id=row.revision_id,
                content_artifact_version_id=artifact.artifact_version_id, updated_at=datetime.now(timezone.utc),
            ))
        else:
            resource_draft.draft_version += 1
            resource_draft.base_revision_id = row.revision_id
            resource_draft.content_artifact_version_id = artifact.artifact_version_id
            resource_draft.updated_at = datetime.now(timezone.utc)
        session.add(ResourceRevisionModel(
            revision_id=row.revision_id, resource_id=definition.agent_id, revision_number=next_number,
            content_artifact_version_id=artifact.artifact_version_id,
            revision_status=RevisionStatus.DRAFT, created_at=datetime.now(timezone.utc),
        ))
        # Reverse-index materialisation is intentionally asynchronous. The
        # immutable revision remains the source of truth and the durable
        # outbox can retry a failed index worker without half-publishing it.
        session.add(OutboxEventModel(
            event_id=uuid4(), aggregate_type="agent_revision", aggregate_id=row.revision_id,
            event_type="agent_revision.index_requested", purpose="agent_revision_index",
            payload={"agent_id": str(definition.agent_id), "revision_id": str(row.revision_id),
                     "owner_scope": definition.owner_scope}, created_at=datetime.now(timezone.utc),
        ))
        draft.body = dict(body)
        draft.content_hash = content_hash
        draft.base_revision_id = row.revision_id
        session.flush()
        return row

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

    def run_isolated_runtime_trial(self, agent_id: UUID, *, draft_version: int, budget: dict,
                                   fixed_input: dict | None = None) -> dict:
        """Execute a draft through the same durable worker/invocation boundary.

        A trial receives a transient, owner-scoped frozen revision and an
        isolated WorkflowRun. The fake Atlas transport is server-owned and
        deterministic; browser callers can influence only typed input/budget.
        """
        import httpx
        from src.domain.agent.invocation_service import AgentInvocationService
        from src.domain.provider.atlascloud import AtlasCloudAdapter
        from src.domain.runtime.runtime_service import RuntimeService
        from src.domain.runtime.worker import RuntimeWorker
        from src.infra.db.models import (CredentialBindingModel, ToolInvocationModel,
                                         WorkflowModel, WorkflowRevisionModel,
                                         NodeRunAttemptModel, NodeRunModel)
        from src.schemas.enums import RevisionStatus
        from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot

        source = self.get_definition(agent_id)
        draft = self.get_draft(agent_id)
        if draft.draft_version != draft_version:
            raise ConflictError("AgentDraft compare-and-swap conflict", details=self._draft_conflict_details(draft))
        from src.domain.agent.agent_service import validate_agent
        body = dict(draft.body or {})
        validate_agent(body)
        # Test provider output is server-controlled and deliberately generic;
        # a typed output schema still decides whether this trial succeeds.
        tool_call: dict | None = None
        if body.get("tool_revision_refs"):
            first_tool_revision = UUID(str(body["tool_revision_refs"][0]))
            with self._factory() as session:
                tool_revision = session.get(ToolRevisionModel, first_tool_revision)
                operation = next((item for item in (tool_revision.body or {}).get("operations", []) if isinstance(item, dict)), None) if tool_revision else None
            if operation is not None:
                tool_call = {"tool_revision_id": str(first_tool_revision), "operation_id": str(operation.get("id", "")),
                             "requested_scopes": [], "disclosure_fields": list(operation.get("disclosure_fields", [])), "input": {}}
        class _TrialAtlas:
            def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
                output = {"tool_calls": [tool_call]} if tool_call is not None else {"text": "trial"}
                return httpx.Response(200, request=httpx.Request(method, url), json={"data": [output], "model_version": "atlascloud-trial"})

        trial_def = self.create_definition(name=f"trial:{source.name}", description="isolated studio trial", agent_kind="configurable", owner_scope=source.owner_scope)
        trial_rev = self.create_revision(trial_def.agent_id, body)
        self.promote_revision(trial_rev.revision_id)
        owner_kind, owner_id = source.owner_scope.split(":", 1)
        owner = OwnerScope(kind=owner_kind, id=UUID(owner_id))
        workflow_id, workflow_revision_id = uuid4(), uuid4()
        graph = {"nodes": [{"id": "trial-agent", "type": "agent_invoke"}], "edges": []}
        with self._factory.begin() as session:
            session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=source.owner_scope))
            session.add(WorkflowRevisionModel(revision_id=workflow_revision_id, workflow_id=workflow_id, revision_number=1,
                graph_hash="trial", execution_hash="trial", registry_snapshot_id=uuid4(), graph=graph, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=datetime.now(timezone.utc)))
        runtime = RuntimeService(self._factory)
        plan = CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=workflow_revision_id,
            registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, budget_limits=dict(budget), plan_hash="studio-trial")
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner, input_snapshot=dict(fixed_input or {}))
        runtime.start_run(run.run_id)
        with self._factory.begin() as session:
            node = session.query(NodeRunModel).filter(NodeRunModel.run_id == run.run_id).one()
            attempt = session.query(NodeRunAttemptModel).filter(NodeRunAttemptModel.node_run_id == node.node_run_id).one()
            runtime_input = {**dict(fixed_input or {}), "agent_revision_id": str(trial_rev.revision_id)}
            # Credentials never cross the HTTP boundary.  For an isolated
            # Studio trial we may select the owner's already-bound frozen Tool
            # revision, then pass only its opaque binding ID to the broker.
            # The broker still decrypts and authorizes it server-side.
            bindings: dict[str, str] = {}
            for tool_revision_id in trial_rev.tool_revision_refs:
                bound = session.scalar(select(CredentialBindingModel).where(
                    CredentialBindingModel.owner_scope == source.owner_scope,
                    CredentialBindingModel.tool_revision_id == tool_revision_id,
                    CredentialBindingModel.status == "active",
                ).order_by(CredentialBindingModel.created_at.desc()))
                if bound is not None:
                    bindings[str(tool_revision_id)] = str(bound.binding_id)
            if bindings:
                runtime_input["tool_bindings"] = bindings
            attempt.fixed_input = runtime_input
        leased = runtime.set_attempt_running(attempt.attempt_id, "studio-trial-worker")
        invocation = AgentInvocationService(self._factory, adapter=AtlasCloudAdapter(transport=_TrialAtlas(), api_key="studio-trial", base_url="https://atlas.invalid"))
        try:
            result = RuntimeWorker(self._factory, agent_invocations=invocation).execute_attempt(leased.attempt_id)
            status, failure_owner = str(result["status"]), None
        except ValidationError_ as exc:
            status, failure_owner = "failed", (exc.details or {}).get("field", "runtime")
        # A Studio trial deliberately uses a server-owned mock response for a
        # Tool dispatch.  It exercises the same credential, entitlement,
        # disclosure and outbox state machine as production without allowing a
        # browser dry-run to create an external provider side effect.
        if status == "waiting_tool":
            from src.domain.agent.tool_broker import ToolBroker
            broker = ToolBroker(self._factory)
            class _TrialToolTransport:
                def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
                    return httpx.Response(200, request=httpx.Request(method, url),
                                          json={"trial": "simulated"},
                                          headers={"content-type": "application/json"})
            with self._factory() as session:
                events = list(session.scalars(select(OutboxEventModel.event_id).join(
                    ToolInvocationModel, OutboxEventModel.aggregate_id == ToolInvocationModel.invocation_id,
                ).join(
                    NodeRunAttemptModel, ToolInvocationModel.node_run_attempt_id == NodeRunAttemptModel.attempt_id,
                ).join(
                    NodeRunModel, NodeRunAttemptModel.node_run_id == NodeRunModel.node_run_id,
                ).where(
                    NodeRunModel.run_id == run.run_id,
                    OutboxEventModel.aggregate_type == "tool_invocation",
                    OutboxEventModel.purpose == "tool_dispatch",
                    OutboxEventModel.published_at.is_(None),
                )))
            for event_id in events:
                dispatch_status = broker.consume_dispatch_event(event_id, transport=_TrialToolTransport())
                if dispatch_status != "completed":
                    status, failure_owner = "failed", "tool_dispatch"
                    break
            else:
                status = "completed"
        with self._factory() as session:
            traces = list(session.scalars(select(ArtifactVersionModel).where(
                ArtifactVersionModel.schema_id == "toonflow.agent_sop_trace",
                ArtifactVersionModel.created_by_run_id == run.run_id,
            ).order_by(ArtifactVersionModel.created_at)))
        trial = self.dry_run_draft(agent_id, draft_version=draft_version, budget=budget, fixed_input=fixed_input)
        # Trace bodies are produced by AgentInvocationService and intentionally
        # contain hashes/IDs/disclosure IDs only, never prompt or credentials.
        timeline = [{"phase": row.content_json.get("phase"), "failure_owner": row.content_json.get("failure_owner"),
                     "tool_disclosures": row.content_json.get("tool_disclosures", []), "artifact_version_id": str(row.artifact_version_id)}
                    for row in traces]
        with self._factory() as session:
            tool_rows = list(session.scalars(select(ToolInvocationModel).join(NodeRunAttemptModel).join(NodeRunModel).where(
                NodeRunModel.run_id == run.run_id,
            )))
        timeline.extend({"phase": "tool_dispatch", "status": row.status,
                         "tool_disclosures": [{"tool_revision_id": str(row.tool_revision_id),
                                                "operation_id": row.operation_id,
                                                "fields": list(row.disclosure_manifest or [])}],
                         "tool_invocation_id": str(row.invocation_id)} for row in tool_rows)
        with self._factory.begin() as session:
            trial_row = session.get(AgentTrialRunModel, UUID(str(trial["trial_id"])), with_for_update=True)
            if trial_row is not None:
                trial_row.status, trial_row.failure_owner = status, failure_owner
                trial_row.runtime_run_id = run.run_id
                trial_row.runtime_node_run_id = leased.node_run_id
                trial_row.runtime_attempt_id = leased.attempt_id
                trial_row.runtime_agent_revision_id = trial_rev.revision_id
        trial.update({"status": status, "failure_owner": failure_owner, "runtime_run_id": str(run.run_id),
                      "runtime_trial_agent_revision_id": str(trial_rev.revision_id), "runtime_timeline": timeline})
        return trial

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

    def clone_rebind_requirements(self, agent_id: UUID) -> list[str]:
        """Frozen Tool revision IDs for which a clone must bind its own secret."""
        return sorted({str(value) for value in (self.get_draft(agent_id).body or {}).get("tool_revision_refs", [])})

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
        from src.domain.agent.agent_service import validate_agent
        validate_agent(body)
        content_hash = _compute_hash(body)
        # Determine next revision number
        with self._factory.begin() as session:
            # Fetch agent definition for agent_kind
            def_row = session.get(AgentDefinitionModel, agent_id)
            if def_row is None:
                raise NotFoundError("AgentDefinition", str(agent_id))
            agent_kind = def_row.agent_kind

            latest = session.scalar(
                select(AgentRevisionModel)
                .where(AgentRevisionModel.agent_id == agent_id)
                .order_by(AgentRevisionModel.revision_number.desc())
                .limit(1)
            )
            next_number = (latest.revision_number + 1) if latest else 1
            if agent_kind == "managed_preset" and latest is not None:
                raise ForbiddenError("Managed preset Agent revisions are platform-locked")
            self._validate_dependencies(session, def_row, body)

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
            session.add(OutboxEventModel(
                event_id=uuid4(), aggregate_type="agent_revision", aggregate_id=row.revision_id,
                event_type="agent_revision.index_requested", purpose="agent_revision_index",
                payload={"agent_id": str(agent_id), "revision_id": str(row.revision_id),
                         "owner_scope": def_row.owner_scope}, created_at=datetime.now(timezone.utc),
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
            # A damaged/imported Skill row must not become an indirect Agent
            # invocation path. Normal Skill writes already enforce this; the
            # submit-time recheck closes the historical-data bypass.
            from src.domain.skill.skill_service import validate_skill
            try:
                validate_skill(dict(skill_revision.body or {}))
            except ValidationError_ as exc:
                raise ValidationError_("Agent dependency SkillRevision is not a valid non-executable Skill",
                                       details={"field": f"skill_revision_refs[{index}]", "cause": exc.details}) from exc
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
            cycle_path = SqlAgentRepository._forbidden_dependency_path(dict(revision.body or {}))
            if cycle_path is not None:
                raise ValidationError_(
                    "ToolRevision cannot depend on Agent, Skill, Workflow, or Recipe revisions",
                    details={"field": f"tool_revision_refs[{index}].{cycle_path}"},
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

    @staticmethod
    def _forbidden_dependency_path(value: object, path: str = "") -> str | None:
        """Find a stored Tool dependency that could make Agent references cyclic."""
        forbidden = {"agent_revision_id", "agent_revision_refs", "skill_revision_id", "skill_revision_refs",
                     "workflow_revision_id", "workflow_revision_refs", "recipe_revision_id", "recipe_revision_refs"}
        if isinstance(value, dict):
            for key, nested in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                if str(key) in forbidden:
                    return next_path
                found = SqlAgentRepository._forbidden_dependency_path(nested, next_path)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                found = SqlAgentRepository._forbidden_dependency_path(nested, f"{path}[{index}]")
                if found is not None:
                    return found
        return None

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
