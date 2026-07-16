"""
ToonFlow Backend — SQLAlchemy ORM Models

First migration: identity + project + workflow data contracts (Foundation schema fence).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, String, DateTime, ForeignKey, Text, Integer, Float,
    JSON, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from src.infra.db.base import Base
from src.schemas.enums import (
    AccountStatus, ProjectStatus, RevisionStatus, RunStatus,
    AttemptStatus, NodeRunStatus, HumanTaskStatus,
    ForEachMode, MapItemStatus,
    BlobStatus, UploadSessionStatus,
)


class UserAccountModel(Base):
    __tablename__ = "user_accounts"

    account_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    status: AccountStatus = Column(SAEnum(AccountStatus), default=AccountStatus.ACTIVE, nullable=False)  # type: ignore[assignment]
    owner_scope = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectModel(Base):
    __tablename__ = "projects"

    project_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    status: ProjectStatus = Column(SAEnum(ProjectStatus), default=ProjectStatus.ACTIVE, nullable=False)  # type: ignore[assignment]
    default_entry = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowModel(Base):
    __tablename__ = "workflows"

    workflow_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkflowDraftModel(Base):
    __tablename__ = "workflow_drafts"

    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.workflow_id"), primary_key=True)
    draft_version = Column(Integer, default=1, nullable=False)
    base_revision_id = Column(UUID(as_uuid=True), nullable=True)
    graph = Column(JSON, default=dict)
    config = Column(JSON, default=dict)
    layout = Column(JSON, default=dict)
    graph_hash = Column(String(64), default="")
    layout_hash = Column(String(64), default="")
    execution_hash = Column(String(64), default="")
    # full_draft_hash = sha256(graph_hash | layout_hash | execution_hash | draft_version).
    # The activate + save path uses this for compare-and-swap so that two
    # layout-only saves cannot both pass a graph_hash-only CAS.
    full_draft_hash = Column(String(64), default="", nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowRevisionModel(Base):
    __tablename__ = "workflow_revisions"

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.workflow_id"), nullable=False)
    revision_number = Column(Integer, nullable=False)
    graph_hash = Column(String(64), nullable=False)
    execution_hash = Column(String(64), nullable=False)
    registry_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    # Revisions are immutable execution inputs, not merely hash references.
    graph = Column(JSON, default=dict, nullable=False)
    config = Column(JSON, default=dict, nullable=False)
    layout = Column(JSON, default=dict, nullable=False)
    revision_status: RevisionStatus = Column(SAEnum(RevisionStatus), default=RevisionStatus.ACTIVE)  # type: ignore[assignment]
    created_at = Column(DateTime, default=datetime.utcnow)


class CompiledExecutionPlanModel(Base):
    """Immutable successful (or failed) compiler result for a revision."""

    __tablename__ = "compiled_execution_plans"

    plan_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_revision_id = Column(UUID(as_uuid=True), ForeignKey("workflow_revisions.revision_id"), nullable=False, index=True)
    registry_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(32), nullable=False, default="succeeded")
    plan_hash = Column(String(128), nullable=False)
    compiler_version = Column(String(64), nullable=False)
    plan_json = Column(JSON, nullable=False, default=dict)
    diagnostics = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkflowTemplateModel(Base):
    """Immutable, version-pinned workflow template package (TF-WF-009)."""

    __tablename__ = "workflow_templates"

    template_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    # A template is always pinned to a revision, never a mutable workflow.
    workflow_revision_id = Column(UUID(as_uuid=True), ForeignKey("workflow_revisions.revision_id"), nullable=False)
    manifest = Column(JSON, default=dict, nullable=False)
    parameter_schema = Column(JSON, default=dict, nullable=False)
    default_mapping = Column(JSON, default=dict, nullable=False)
    visibility = Column(String(32), default="private", nullable=False)
    provenance = Column(String(64), default="platform", nullable=False)
    revision_status = Column(SAEnum(RevisionStatus), default=RevisionStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WorkflowTemplateInstanceModel(Base):
    """Durable lineage for a template-derived project and editable draft."""

    __tablename__ = "workflow_template_instances"

    instance_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("workflow_templates.template_id"), nullable=False, index=True)
    template_revision_id = Column(UUID(as_uuid=True), ForeignKey("workflow_revisions.revision_id"), nullable=False)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.project_id"), nullable=False, index=True)
    workflow_id = Column(UUID(as_uuid=True), ForeignKey("workflows.workflow_id"), nullable=False, unique=True)
    dependency_resolution = Column(JSON, default=dict, nullable=False)
    replacement_mapping = Column(JSON, default=dict, nullable=False)
    attribution_manifest = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_revision_id = Column(UUID(as_uuid=True), ForeignKey("workflow_revisions.revision_id"), nullable=False)
    compiled_plan_id = Column(UUID(as_uuid=True), nullable=False)
    owner_scope = Column(String(255), nullable=False)
    input_snapshot = Column(JSON, default=dict)
    status: RunStatus = Column(SAEnum(RunStatus), default=RunStatus.QUEUED)  # type: ignore[assignment]
    created_at = Column(DateTime, default=datetime.utcnow)


class NodeRunModel(Base):
    __tablename__ = "node_runs"

    node_run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False)
    node_instance_id = Column(String(255), nullable=False)
    node_type_id = Column(String(255), nullable=False)
    status: NodeRunStatus = Column(SAEnum(NodeRunStatus), default=NodeRunStatus.PENDING, nullable=False)  # type: ignore[assignment]


class NodeRunAttemptModel(Base):
    __tablename__ = "node_run_attempts"

    attempt_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_run_id = Column(UUID(as_uuid=True), ForeignKey("node_runs.node_run_id"), nullable=False)
    attempt_number = Column(Integer, default=1)
    execution_epoch = Column(Integer, default=1)
    lease_id = Column(String(255), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    fixed_input = Column(JSON, default=dict, nullable=False)
    status: AttemptStatus = Column(SAEnum(AttemptStatus), default=AttemptStatus.PENDING)  # type: ignore[assignment]
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type = Column(String(255), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String(255), nullable=False)
    payload = Column(JSON, default=dict)
    purpose = Column(String(64), nullable=False)
    # Foundation scope contract: dedupe_key is a stable per-purpose
    # fingerprint that the dispatcher uses to suppress duplicate replays.
    # ``provider_dispatch`` rows pin ``dedupe_key`` to the
    # ``provider_attempt_id``; ``result_publish`` rows pin it to the
    # same id.  The partial unique index keeps the contract enforced at
    # the database boundary.
    dedupe_key = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)
    retry_count = Column(Integer, default=0)


class ArtifactVersionModel(Base):
    """Durable artifact identity used by provider output bindings.

    Foundation migration stores the full ArtifactVersion schema (id,
    schema identity, content, lineage) so we can replay cross-owner
    references against the same database.
    """

    __tablename__ = "artifact_versions"

    artifact_version_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    schema_id = Column(String(255), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    owner_scope = Column(String(255), nullable=False, index=True)
    content_uri = Column(Text, default="", nullable=False)
    content_json = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(128), default="", nullable=False)
    created_by_run_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    lineage_input_refs = Column(JSON, default=list, nullable=False)
    blob_uri = Column(Text, default="", nullable=False)
    metadata_json = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ResourceModel(Base):
    __tablename__ = "resources"

    resource_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_type = Column(String(128), nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    # An OC elevated from a World retains this immutable origin on its stable
    # Resource identity and every frozen revision below.
    source_world_revision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_local_id = Column(String(255), nullable=True)
    source_content_hash = Column(String(128), nullable=True)
    elevation_event_id = Column(UUID(as_uuid=True), nullable=True, unique=True)
    # Immutable promotion provenance recorded in the SAME transaction as
    # the Resource row.  ``promotion_source_kind`` is either "output_binding",
    # "selection_record", or "bootstrap" for native-resource creation paths.
    promotion_source_kind = Column(String(32), nullable=False, default="bootstrap")
    promotion_source_ref_id = Column(UUID(as_uuid=True), nullable=True)
    promotion_source_artifact_version_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ResourceDraftModel(Base):
    __tablename__ = "resource_drafts"

    resource_id = Column(UUID(as_uuid=True), ForeignKey("resources.resource_id"), primary_key=True)
    draft_version = Column(Integer, nullable=False, default=1)
    base_revision_id = Column(UUID(as_uuid=True), nullable=True)
    content_artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    stale_reason = Column(JSON, nullable=True)


class ResourceRevisionModel(Base):
    __tablename__ = "resource_revisions"
    __table_args__ = (UniqueConstraint("resource_id", "revision_number", name="uq_resource_revision_number"),)

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("resources.resource_id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    content_artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=False)
    revision_status = Column(SAEnum(RevisionStatus), nullable=False, default=RevisionStatus.ACTIVE)
    created_from_artifact_version_id = Column(UUID(as_uuid=True), nullable=True)
    source_world_revision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_local_id = Column(String(255), nullable=True)
    source_content_hash = Column(String(128), nullable=True)
    elevation_event_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ResourceGrantSnapshotModel(Base):
    """Durable evidence that a fixed external resource revision was granted."""

    __tablename__ = "resource_grant_snapshots"

    grant_snapshot_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_revision_id = Column(UUID(as_uuid=True), ForeignKey("resource_revisions.revision_id"), nullable=False, index=True)
    grantee_scope = Column(String(255), nullable=False, index=True)
    # Capability actions are intentionally separate.  A historical grant
    # snapshot never lets reference imply execution or redistribution.
    capability_actions = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class ProviderInvocationAttemptModel(Base):
    __tablename__ = "provider_invocation_attempts"

    provider_attempt_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_run_attempt_id = Column(UUID(as_uuid=True), ForeignKey("node_run_attempts.attempt_id"), nullable=False, index=True)
    provider_id = Column(String(255), nullable=False)
    model_id = Column(String(255), nullable=False)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    request_body_hash = Column(String(128), nullable=False)
    status: AttemptStatus = Column(SAEnum(AttemptStatus), default=AttemptStatus.PENDING, nullable=False)  # type: ignore[assignment]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProviderInvocationRecordModel(Base):
    __tablename__ = "provider_invocation_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_attempt_id = Column(UUID(as_uuid=True), ForeignKey("provider_invocation_attempts.provider_attempt_id"), nullable=False, unique=True)
    provider_id = Column(String(255), nullable=False)
    model_id = Column(String(255), nullable=False)
    model_version = Column(String(255), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_body_hash = Column(String(128), nullable=False)
    response_fingerprint = Column(String(255), nullable=False)
    usage = Column(JSON, default=dict, nullable=False)
    actual_cost = Column(Float, default=0.0, nullable=False)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=False)


class ProviderOutputBindingModel(Base):
    __tablename__ = "provider_output_bindings"

    binding_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(UUID(as_uuid=True), ForeignKey("provider_invocation_records.record_id"), nullable=False, index=True)
    output_artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=False)
    output_index = Column(Integer, default=0, nullable=False)
    output_label = Column(String(255), default="", nullable=False)
    # Owner scope denormalised from the producer Run so the promotion gate
    # can verify cross-tenant eligibility without re-walking the Run tree.
    owner_scope = Column(String(255), nullable=False, default="", index=True)


class WorkflowTaskBindingModel(Base):
    __tablename__ = "workflow_task_bindings"

    binding_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_run_attempt_id = Column(UUID(as_uuid=True), ForeignKey("node_run_attempts.attempt_id"), nullable=False)
    provider_attempt_id = Column(UUID(as_uuid=True), ForeignKey("provider_invocation_attempts.provider_attempt_id"), nullable=False)
    provider_task_id = Column(String(255), nullable=False, unique=True)
    task_status = Column(String(64), default="pending", nullable=False)


class HumanTaskModel(Base):
    __tablename__ = "human_tasks"

    task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_kind = Column(String(64), nullable=False)
    owner_layer = Column(String(64), nullable=False)
    owner_revision_id = Column(UUID(as_uuid=True), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False)
    node_run_id = Column(UUID(as_uuid=True), ForeignKey("node_runs.node_run_id"), nullable=False)
    attempt_id = Column(UUID(as_uuid=True), ForeignKey("node_run_attempts.attempt_id"), nullable=False)
    input_snapshot_refs = Column(JSON, default=list, nullable=False)
    assignee_scope = Column(String(255), nullable=True)
    policy_strength = Column(String(64), nullable=False)
    schema_ref = Column(String(255), default="", nullable=False)
    timeout_policy = Column(JSON, default=dict, nullable=False)
    status: HumanTaskStatus = Column(SAEnum(HumanTaskStatus), default=HumanTaskStatus.PENDING, nullable=False)  # type: ignore[assignment]
    task_version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class HumanTaskDecisionModel(Base):
    """Immutable, actor-attributed terminal decision for a human task.

    The unique constraints are the database fence for both competing terminal
    submissions and retrying the same idempotency token after a response loss.
    """

    __tablename__ = "human_task_decisions"
    __table_args__ = (
        UniqueConstraint("task_id", "task_version", name="uq_human_task_decision_version"),
        UniqueConstraint("task_id", "idempotency_token", name="uq_human_task_decision_token"),
    )

    decision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("human_tasks.task_id"), nullable=False, index=True)
    task_version = Column(Integer, nullable=False)
    action = Column(String(32), nullable=False)
    actor_id = Column(UUID(as_uuid=True), nullable=False)
    actor_scope = Column(String(255), nullable=False)
    typed_payload = Column(JSON, default=dict, nullable=False)
    notes = Column(Text, default="", nullable=False)
    policy_evidence_refs = Column(JSON, default=list, nullable=False)
    idempotency_token = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Generic business nodes / WorkbenchTask (TF-WF-010)
# ---------------------------------------------------------------------------


class CandidateSetModel(Base):
    """Ordered candidate ArtifactVersion references; candidate content is never copied."""

    __tablename__ = "candidate_sets"

    candidate_set_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=True, index=True)
    node_run_id = Column(UUID(as_uuid=True), ForeignKey("node_runs.node_run_id"), nullable=True)
    candidate_refs = Column(JSON, default=list, nullable=False)
    failed_candidates = Column(JSON, default=list, nullable=False)
    cost_allocation = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SelectionRecordModel(Base):
    """Append-only human/model ranking over a fixed CandidateSet."""

    __tablename__ = "selection_records"

    selection_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_set_id = Column(UUID(as_uuid=True), ForeignKey("candidate_sets.candidate_set_id"), nullable=False, index=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    ranking = Column(JSON, default=list, nullable=False)
    selected_refs = Column(JSON, default=list, nullable=False)
    actor_or_model = Column(String(255), nullable=False)
    rubric_revision = Column(String(255), nullable=False)
    rationale = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ResourceCommitModel(Base):
    """Workflow-owned immutable ResourceRef publication boundary."""

    __tablename__ = "resource_commits"
    __table_args__ = (
        UniqueConstraint("resource_id", "revision_number", name="uq_resource_commits_revision"),
    )

    commit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("human_tasks.task_id"), nullable=False, index=True)
    resource_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    revision_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    revision_number = Column(Integer, nullable=False)
    resource_type = Column(String(255), nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    source_artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=False)
    expected_draft_version = Column(Integer, nullable=False)
    committed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Registry (TF-WF-002)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agent / Skill / Recipe / Tool — ORM models (TF-ASR-001)
# ---------------------------------------------------------------------------


class AgentDefinitionModel(Base):
    __tablename__ = "agent_definitions"

    agent_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    agent_kind = Column(String(64), nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    cloned_from_agent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AgentDraftModel(Base):
    """Mutable authoring state; immutable AgentRevision never doubles as it."""

    __tablename__ = "agent_drafts"

    agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_definitions.agent_id"), primary_key=True)
    draft_version = Column(Integer, nullable=False, default=1)
    base_revision_id = Column(UUID(as_uuid=True), nullable=True)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AgentTrialRunModel(Base):
    """Isolated, non-business Studio test-run for one frozen draft snapshot."""

    __tablename__ = "agent_trial_runs"

    trial_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_definitions.agent_id"), nullable=False, index=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    draft_version = Column(Integer, nullable=False)
    fixed_body = Column(JSON, nullable=False, default=dict)
    fixed_input = Column(JSON, nullable=False, default=dict)
    budget = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="completed")
    failure_owner = Column(String(128), nullable=True)
    # The Studio trial is an authoring record, but its execution must remain
    # attributable to the exact durable runtime attempt it exercised.  These
    # links allow a typed RequestInput to pause/resume that attempt after a
    # browser refresh without re-running a different draft.
    runtime_run_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    runtime_node_run_id = Column(UUID(as_uuid=True), nullable=True)
    runtime_attempt_id = Column(UUID(as_uuid=True), nullable=True)
    runtime_agent_revision_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AgentTrialStepTraceModel(Base):
    __tablename__ = "agent_trial_step_traces"

    trace_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trial_id = Column(UUID(as_uuid=True), ForeignKey("agent_trial_runs.trial_id"), nullable=False, index=True)
    step_id = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False)
    usage = Column(JSON, nullable=False, default=dict)
    tool_disclosures = Column(JSON, nullable=False, default=list)
    failure_owner = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AgentTrialRequestInputModel(Base):
    __tablename__ = "agent_trial_request_inputs"
    task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trial_id = Column(UUID(as_uuid=True), ForeignKey("agent_trial_runs.trial_id"), nullable=False, index=True)
    schema_ref = Column(String(255), nullable=False)
    question = Column(Text, nullable=False)
    input_schema = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="waiting")
    task_version = Column(Integer, nullable=False, default=1)
    answer = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AgentRevisionModel(Base):
    __tablename__ = "agent_revisions"

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agent_definitions.agent_id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    base_hash = Column(String(64), nullable=True, default=None)
    status = Column(String(32), default="draft", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("agent_id", "revision_number", name="uq_agent_revisions_number"),
    )


class SkillContentModel(Base):
    __tablename__ = "skill_contents"

    skill_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    base_hash = Column(String(64), nullable=True, default=None)
    status = Column(String(32), default="draft", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SkillAssemblyPlanModel(Base):
    __tablename__ = "skill_assembly_plans"

    plan_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skill_contents.skill_id"), nullable=False, index=True)
    agent_revision_id = Column(UUID(as_uuid=True), nullable=False)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SkillPolicyStateModel(Base):
    __tablename__ = "skill_policy_states"
    revision_id = Column(UUID(as_uuid=True), ForeignKey("skill_revisions.revision_id"), primary_key=True)
    state = Column(String(32), nullable=False, default="active")
    reason = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SkillPackageEmbedModel(Base):
    __tablename__ = "skill_package_embeds"
    embed_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_revision_id = Column(UUID(as_uuid=True), ForeignKey("skill_revisions.revision_id"), nullable=False, index=True)
    installer_scope = Column(String(255), nullable=False, index=True)
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    grant_snapshot_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ToolDefinitionModel(Base):
    __tablename__ = "tool_definitions"

    tool_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    provider_type = Column(String(64), nullable=False, default="")
    approval_status = Column(String(32), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ToolRevisionModel(Base):
    __tablename__ = "tool_revisions"

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_id = Column(UUID(as_uuid=True), ForeignKey("tool_definitions.tool_id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    base_hash = Column(String(64), nullable=True, default=None)
    status = Column(String(32), default="draft", nullable=False)
    approval_status = Column(String(32), default="pending", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("tool_id", "revision_number", name="uq_tool_revisions_number"),
    )


class MediaRecipeDefinitionModel(Base):
    __tablename__ = "media_recipe_definitions"

    recipe_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    owner_scope = Column(String(255), nullable=False, index=True)
    recipe_type = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CredentialBindingModel(Base):
    __tablename__ = "credential_bindings"

    binding_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    tool_revision_id = Column(UUID(as_uuid=True), ForeignKey("tool_revisions.revision_id"), nullable=False, index=True)
    scopes = Column(JSON, default=list, nullable=False)
    encrypted_secret = Column(Text, nullable=False)
    status = Column(String(32), default="active", nullable=False)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class ToolInvocationModel(Base):
    __tablename__ = "tool_invocations"

    invocation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_revision_id = Column(UUID(as_uuid=True), ForeignKey("tool_revisions.revision_id"), nullable=False, index=True)
    credential_binding_id = Column(UUID(as_uuid=True), ForeignKey("credential_bindings.binding_id"), nullable=False, index=True)
    # Optional only for management dry-runs; workflow invocations must bind a
    # concrete attempt so side effects and sanitized outputs have lineage.
    node_run_attempt_id = Column(UUID(as_uuid=True), ForeignKey("node_run_attempts.attempt_id"), nullable=True, index=True)
    output_artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    operation_id = Column(String(255), nullable=False)
    input_fingerprint = Column(String(128), nullable=False)
    disclosure_manifest = Column(JSON, default=list, nullable=False)
    disclosure_manifest_hash = Column(String(128), nullable=False, default="")
    policy_decision = Column(String(32), nullable=False)
    decision_refs = Column(JSON, default=list, nullable=False)
    usage = Column(JSON, default=dict, nullable=False)
    result_fingerprint = Column(String(128), nullable=False, default="")
    idempotency_key = Column(String(255), nullable=False, default="")
    status = Column(String(32), nullable=False, default="authorized")
    dispatch_lease_owner = Column(String(255), nullable=True)
    dispatch_lease_expires_at = Column(DateTime, nullable=True)
    external_submission_started_at = Column(DateTime, nullable=True)
    reserved_cost = Column(Float, nullable=False, default=0.0)
    actual_cost = Column(Float, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    cancellation_requested_at = Column(DateTime, nullable=True)
    reconciled_at = Column(DateTime, nullable=True)
    late_result_quarantined = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SkillRevisionModel(Base):
    """Immutable SkillRevision; drafts remain on SkillContentModel."""

    __tablename__ = "skill_revisions"
    __table_args__ = (UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"),)

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skill_contents.skill_id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MediaRecipeRevisionModel(Base):
    __tablename__ = "media_recipe_revisions"

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id = Column(UUID(as_uuid=True), ForeignKey("media_recipe_definitions.recipe_id"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    body = Column(JSON, default=dict, nullable=False)
    content_hash = Column(String(64), nullable=False, default="")
    base_hash = Column(String(64), nullable=True, default=None)
    status = Column(String(32), default="draft", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("recipe_id", "revision_number", name="uq_media_recipe_revisions_number"),
    )


class NodeDefinitionStatusEnum(str, enum.Enum):
    """Lifecycle bucket for a NodeDefinitionRevision row."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class NodeDefinitionModel(Base):
    """Durable ``NodeDefinitionRevision`` row.

    Each unique ``(node_type_id, semantic_version)`` is a single revision.  The
    registry status (draft / active / retired) is independent of versioning
    so multiple rows may share the same ``node_type_id``.
    """

    __tablename__ = "node_definitions"
    __table_args__ = (
        UniqueConstraint(
            "node_type_id", "semantic_version",
            name="uq_node_definitions_type_version",
        ),
    )

    revision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_type_id = Column(String(255), nullable=False, index=True)
    semantic_version = Column(String(64), nullable=False)
    # Body is stored as JSON; Pydantic round-trips through ``NodeDefinitionRevision``.
    body = Column(JSON, default=dict, nullable=False)
    # Computed from sorted body JSON; supports idempotent content-replace.
    content_hash = Column(String(64), nullable=False, default="")
    status: NodeDefinitionStatusEnum = Column(  # type: ignore[assignment]
        SAEnum(NodeDefinitionStatusEnum, name="nodedefinitionstatus"),
        default=NodeDefinitionStatusEnum.DRAFT,
        nullable=False,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ApprovedNodePackageModel(Base):
    __tablename__ = "approved_node_packages"
    package_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("node_definitions.revision_id"), unique=True, nullable=False)
    content_hash = Column(String(128), nullable=False)
    signer_id = Column(String(255), nullable=False)
    signature = Column(String(256), nullable=False)
    approval_id = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class NodeContractTestRunModel(Base):
    __tablename__ = "node_contract_test_runs"
    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    revision_id = Column(UUID(as_uuid=True), ForeignKey("node_definitions.revision_id"), nullable=False, index=True)
    case_name = Column(String(64), nullable=False)
    passed = Column(Boolean, nullable=False)
    evidence = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConverterRevisionModel(Base):
    """Durable type-converter row keyed on the immutable 4-tuple."""

    __tablename__ = "converter_revisions"
    __table_args__ = (
        UniqueConstraint(
            "from_schema_id", "from_schema_version",
            "to_schema_id", "to_schema_version",
            name="uq_converter_revisions_four_tuple",
        ),
    )

    converter_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_schema_id = Column(String(255), nullable=False)
    from_schema_version = Column(Integer, nullable=False)
    to_schema_id = Column(String(255), nullable=False)
    to_schema_version = Column(Integer, nullable=False)
    executor_digest = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RegistrySnapshotModel(Base):
    """Frozen, content-hashed snapshot of an active registry state.

    Snapshots are immutable: rows are inserted once with their
    ``schema_hash`` and never updated.  ``node_definitions`` and
    ``converter_revisions`` are persisted as JSON blobs so a snapshot is
    fully self-contained for compilation replay.
    """

    __tablename__ = "registry_snapshots"

    snapshot_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schema_hash = Column(String(64), nullable=False, index=True)
    node_definitions = Column(JSON, default=dict, nullable=False)
    converter_revisions = Column(JSON, default=dict, nullable=False)
    is_frozen = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Control Flow — Condition / Join / ForEach / MapItem / Subworkflow (TF-CF-001)
# All state lives in PostgreSQL.  Map item state is bounded and claimable,
# Fold checkpoints live in the frozen ForEach config, and Subworkflow rows bind
# a parent node to a fixed-revision child run.
# ---------------------------------------------------------------------------


class ConditionModel(Base):
    """Persistent condition node definition and evaluation state.

    Static validation (operator, value_path, threshold) happens at config time;
    runtime evaluation is delegated to the evaluator interface.
    """

    __tablename__ = "conditions"

    condition_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False, index=True)
    node_instance_id = Column(String(255), nullable=False)
    operator = Column(String(30), nullable=False)  # type: ignore[assignment]
    threshold = Column(JSON, nullable=True)
    value_path = Column(String(255), nullable=True)
    expression = Column(JSON, nullable=True)
    status = Column(String(32), default="pending", nullable=False)  # pending | evaluated | failed
    result = Column(Boolean, nullable=True)
    config = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "node_instance_id", name="uq_conditions_run_node"),
    )


class JoinModel(Base):
    """Persistent join node state — tracks incoming branches and merge result.

    JoinStrategy (and / or / xor / sequential) is validated at config time.
    """

    __tablename__ = "joins"

    join_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False, index=True)
    node_instance_id = Column(String(255), nullable=False)
    strategy = Column(String(30), nullable=False)  # type: ignore[assignment]
    source_node_ids = Column(JSON, default=list, nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending | completed | failed
    result = Column(JSON, nullable=True)
    config = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "node_instance_id", name="uq_joins_run_node"),
    )


class MapItemRunModel(Base):
    """Per-item execution state inside a ForEach loop.

    MapItem states: PENDING → RUNNING → COMPLETED / FAILED / SKIPPED.
    """

    __tablename__ = "map_item_runs"

    map_item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False, index=True)
    node_instance_id = Column(String(255), nullable=False)
    item_key = Column(String(255), nullable=False)
    # Input order is execution data, not presentation.  Map completion may be
    # concurrent, but consumers always receive outputs in this order.
    item_index = Column(Integer, nullable=False, default=0)
    item_value = Column(JSON, default=dict, nullable=False)
    status: MapItemStatus = Column(String(30), default=MapItemStatus.PENDING, nullable=False)  # type: ignore[assignment]
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    __table_args__ = (
        UniqueConstraint("run_id", "node_instance_id", "item_key", name="uq_map_items_run_node_key"),
    )


class ForEachRunModel(Base):
    """ForEach loop run state — tracks overall collection iteration progress.

    ``config`` freezes bounded execution policy and the latest verified Fold
    checkpoint.  Individual items remain in ``map_item_runs`` so a restart
    never has to recreate or reorder work.
    """

    __tablename__ = "for_each_runs"

    for_each_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False, index=True)
    node_instance_id = Column(String(255), nullable=False)
    mode = Column(String(30), default=ForEachMode.SEQUENTIAL, nullable=False)  # type: ignore[assignment]
    collection_ref = Column(String(255), nullable=True)
    item_count = Column(Integer, default=0, nullable=False)
    completed_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending | running | completed | failed
    config = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("run_id", "node_instance_id", name="uq_for_each_run_node"),
    )


class FoldCheckpointModel(Base):
    """Fenced, append-only accumulator checkpoint for an OrderedMap/Fold."""

    __tablename__ = "fold_checkpoints"
    checkpoint_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    for_each_id = Column(UUID(as_uuid=True), ForeignKey("for_each_runs.for_each_id"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    execution_epoch = Column(Integer, nullable=False)
    accumulator = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("for_each_id", "item_index", "execution_epoch", name="uq_fold_checkpoint_epoch"),
    )


class SubworkflowModel(Base):
    """Binding between a parent node and its fixed-revision child run."""

    __tablename__ = "subworkflows"

    subworkflow_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("workflow_runs.run_id"), nullable=False, index=True)
    node_instance_id = Column(String(255), nullable=False)
    child_run_id = Column(UUID(as_uuid=True), nullable=True)
    parent_node_instance_id = Column(String(255), nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending | running | completed | failed
    config = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("run_id", "node_instance_id", name="uq_subworkflows_run_node"),
    )


# ---------------------------------------------------------------------------
# Blob / Storage (TF-OPS-003)
# ---------------------------------------------------------------------------


class BlobModel(Base):
    """Durable metadata for an immutable content blob.

    ``storage_key`` is the server-only internal address; the public
    ArtifactRef / BlobRef shape exposes ``blob_id`` only.  ``status``
    blocks ``uploading``/``quarantined``/``deletion_pending`` rows from
    being cited as the durable backing of an ArtifactVersion.
    """

    __tablename__ = "blobs"

    blob_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    storage_key = Column(Text, nullable=False, unique=True)
    media_type = Column(String(255), nullable=False, default="application/octet-stream")
    size_bytes = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(128), nullable=False, index=True)
    status: BlobStatus = Column(  # type: ignore[assignment]
        String(32), nullable=False, default=BlobStatus.UPLOADING,
    )
    quarantine_reason = Column(Text, nullable=True)
    durability_receipt = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UploadSessionModel(Base):
    """Durable upload session lifecycle (TF-OPS-003 FR-3)."""

    __tablename__ = "upload_sessions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "idempotency_key", name="uq_upload_sessions_idempotency"),
    )

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blob_id = Column(UUID(as_uuid=True), ForeignKey("blobs.blob_id"), nullable=False, index=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    expected_size_bytes = Column(Integer, nullable=False)
    expected_content_hash = Column(String(128), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    status: UploadSessionStatus = Column(  # type: ignore[assignment]
        String(32), nullable=False, default=UploadSessionStatus.INITIATED,
    )
    part_state = Column(JSON, nullable=False, default=list)
    bytes_received = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class ArtifactBlobRefModel(Base):
    """Durable binding of an ArtifactVersion to a backing Blob.

    Inserted in the SAME transaction as the ArtifactVersion row so the
    BlobReferenceIndex cannot drift away from canonical truth.
    """

    __tablename__ = "artifact_blob_refs"
    __table_args__ = (
        UniqueConstraint("artifact_version_id", "blob_id", "role", name="uq_artifact_blob_refs_triplet"),
    )

    ref_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_version_id = Column(UUID(as_uuid=True), ForeignKey("artifact_versions.artifact_version_id"), nullable=False, index=True)
    blob_id = Column(UUID(as_uuid=True), ForeignKey("blobs.blob_id"), nullable=False, index=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="primary")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BlobReferenceIndexModel(Base):
    """Flat, owner-scoped reverse index for ``delete-check``.

    The full reference set is the union of ``artifact_blob_refs`` plus the
    rows that cite ``blobs.blob_id`` directly (resource_revisions, runs,
    audit).  This index is a derivation of those rows and exists so a
    reconstruction exercise can prove the canonical answer.
    """

    __tablename__ = "blob_reference_index"
    __table_args__ = (
        UniqueConstraint("blob_id", "ref_kind", "ref_id", name="uq_blob_reference_index"),
    )

    index_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blob_id = Column(UUID(as_uuid=True), ForeignKey("blobs.blob_id"), nullable=False, index=True)
    owner_scope = Column(String(255), nullable=False, index=True)
    ref_kind = Column(String(32), nullable=False)  # artifact_version | resource_revision | run | audit
    ref_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LineageEdgeModel(Base):
    """Durable, first-class lineage rows (CANONICAL).

    ``lineage_input_refs`` on ArtifactVersionModel is an immutable
    snapshot written once in the same transaction as the ArtifactVersion
    row and never updated by any rebuild path.  ``LineageEdgeModel`` is
    the authoritative source for the lineage graph; the projection
    table below caches it for cheap replay and is rebuilt from these
    rows.
    """

    __tablename__ = "lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "artifact_version_id", "order_index",
            name="uq_lineage_edges_version_order",
        ),
    )

    edge_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("artifact_versions.artifact_version_id"),
        nullable=False, index=True,
    )
    order_index = Column(Integer, nullable=False)
    source_ref = Column(JSON, nullable=False, default=dict)
    role = Column(String(64), nullable=False, default="input")
    producer = Column(JSON, nullable=False, default=dict)
    transformation = Column(JSON, nullable=False, default=dict)
    captured_policy_refs = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LineageEdgeProjectionModel(Base):
    """Rebuildable projection of ``LineageEdgeModel`` (NON-CANONICAL).

    A rebuild path may freely drop and refill this table without ever
    touching the canonical ArtifactVersion row or its
    ``lineage_input_refs`` snapshot.  This is what makes AC-6 ("delete
    all projections and rebuild") safe: the canonical lineage rows
    plus the immutable snapshot remain intact across the rebuild.
    """

    __tablename__ = "lineage_edges_projections"
    __table_args__ = (
        UniqueConstraint(
            "artifact_version_id", "order_index",
            name="uq_lineage_edges_projections_version_order",
        ),
    )

    projection_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_version_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    order_index = Column(Integer, nullable=False)
    source_ref = Column(JSON, nullable=False, default=dict)
    role = Column(String(64), nullable=False, default="input")
    producer = Column(JSON, nullable=False, default=dict)
    transformation = Column(JSON, nullable=False, default=dict)
    captured_policy_refs = Column(JSON, nullable=False, default=list)
    rebuilt_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditLogModel(Base):
    """Minimal durable audit trail used by TF-OPS-003 FR-8 / FR-11.

    The schema is intentionally narrow: a stable ``ref_kind`` + ``ref_id``
    pair is sufficient to prove that a Blob was cited by an immutable
    record at audit time.  Full audit semantics are owned by TF-OPS-005;
    this table exists so a delete-check never silently orphans an audit
    reference.
    """

    __tablename__ = "audit_log"

    audit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    event_type = Column(String(128), nullable=False)
    blob_id = Column(UUID(as_uuid=True), ForeignKey("blobs.blob_id"), nullable=True, index=True)
    ref_kind = Column(String(64), nullable=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Promotion gate (TF-WF-005 + TF-PLT-003)
# ---------------------------------------------------------------------------


class OutputBindingSupersedeModel(Base):
    """Records when an OutputBinding / SelectionRecord candidate was retired.

    The Foundation contract requires promotion to start from a *valid* and
    *non-superseded* candidate; this row is the only durable source of
    that bit.  When a candidate is re-issued (for example after a model
    rerun), callers insert a row here and the promotion resolver refuses
    to honour the prior binding.
    """

    __tablename__ = "output_binding_supersedes"

    supersede_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope = Column(String(255), nullable=False, index=True)
    ref_kind = Column(String(32), nullable=False)  # "output_binding" | "selection_record"
    ref_id = Column(UUID(as_uuid=True), nullable=False)
    superseded_by_ref_id = Column(UUID(as_uuid=True), nullable=True)
    reason = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("ref_kind", "ref_id", name="uq_output_binding_supersede_target"),
    )
