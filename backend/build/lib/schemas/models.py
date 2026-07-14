"""
ToonFlow Backend — Common Pydantic Models
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import (
    AccountStatus,
    AttemptStatus,
    CredentialStatus,
    EvidenceType,
    GovernanceDecision,
    HumanGateStrength,
    HumanTaskStatus,
    NodeRunStatus,
    ProjectStatus,
    RequirementStatus,
    RevisionStatus,
    RunStatus,
    SessionStatus,
    ToolApprovalStatus,
    WorkbenchActionType,
    WorkbenchScopeKind,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Generic paginated response
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class OwnerScope(BaseModel):
    kind: str  # "user" | "project"
    id: UUID

    @property
    def scoped_id(self) -> str:
        return f"{self.kind}:{self.id}"


class Actor(BaseModel):
    actor_id: UUID
    owner_scope: OwnerScope
    identity_kind: str = "user"  # "user" | "service" | "worker"


class UserAccount(BaseModel):
    account_id: UUID
    email: str
    display_name: str
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Content & Artifacts
# ---------------------------------------------------------------------------

class ArtifactRef(BaseModel):
    artifact_id: UUID
    artifact_version_id: UUID
    schema_id: str
    schema_version: int


class ArtifactVersion(BaseModel):
    artifact_id: UUID
    artifact_version_id: UUID
    schema_id: str
    schema_version: int
    owner_scope: OwnerScope
    content_uri: str | None = None
    content_json: dict[str, Any] | None = None
    created_by_run_id: UUID | None = None
    lineage_input_refs: list[ArtifactRef] = []
    created_at: datetime
    content_hash: str | None = None


class Resource(BaseModel):
    resource_id: UUID
    resource_type: str
    owner_scope: OwnerScope
    created_at: datetime


class ResourceRef(BaseModel):
    resource_id: UUID
    resource_type: str
    revision_id: UUID
    role: str | None = None
    grant_snapshot_id: UUID | None = None


class ResourceDraft(BaseModel):
    resource_id: UUID
    draft_version: int = 1
    base_revision_id: UUID | None = None
    content_artifact_version_id: UUID
    updated_at: datetime


class ResourceRevision(BaseModel):
    resource_id: UUID
    revision_id: UUID
    revision_number: int
    content_artifact_version_id: UUID
    revision_status: RevisionStatus = RevisionStatus.ACTIVE
    created_from_artifact_version_id: UUID | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class Workflow(BaseModel):
    workflow_id: UUID
    owner_scope: OwnerScope
    created_at: datetime


class WorkflowDraft(BaseModel):
    workflow_id: UUID
    draft_version: int
    base_revision_id: UUID | None = None
    graph: dict[str, Any] = {}
    config: dict[str, Any] = {}
    layout: dict[str, Any] = {}
    graph_hash: str = ""
    layout_hash: str = ""
    execution_hash: str = ""
    updated_at: datetime


class WorkflowRevision(BaseModel):
    workflow_id: UUID
    revision_id: UUID
    revision_number: int
    graph_hash: str
    execution_hash: str
    registry_snapshot_id: UUID
    compiled_plan_ids: list[UUID] = []
    revision_status: RevisionStatus = RevisionStatus.ACTIVE
    created_at: datetime


class NodeDefinitionRevision(BaseModel):
    node_type_id: str
    revision_id: UUID
    semantic_version: str
    input_ports: list[PortTypeRef] = []
    output_ports: list[PortTypeRef] = []
    config_schema: dict[str, Any] = {}
    executor_ref: str = ""
    policy_metadata: dict[str, Any] = {}
    ui_metadata: dict[str, Any] = {}


class PortTypeRef(BaseModel):
    port_id: str
    type_id: str
    schema_id: str
    schema_version: int
    cardinality: str  # "required" | "optional" | "list"
    required_policy: list[str] = []


class RegistrySnapshot(BaseModel):
    snapshot_id: UUID
    node_definitions: dict[str, NodeDefinitionRevision] = {}
    converter_revisions: dict[str, str] = {}
    schema_hash: str = ""
    created_at: datetime


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class CompiledExecutionPlan(BaseModel):
    plan_id: UUID
    workflow_revision_id: UUID
    registry_snapshot: RegistrySnapshot
    resolved_graph: dict[str, Any] = {}
    definition_snapshots: dict[str, NodeDefinitionRevision] = {}
    converter_revisions: dict[str, str] = {}
    resolved_input_refs: list[ResourceRef | ArtifactRef] = []
    executor_refs: dict[str, str] = {}
    provider_policy_ref: str = ""
    capability_snapshots: list[str] = []
    policy_revisions: list[str] = []
    budget_limits: dict[str, Any] = {}
    compiler_version: str = "1.0"
    plan_hash: str = ""
    created_at: datetime


class WorkflowRun(BaseModel):
    run_id: UUID
    workflow_revision_id: UUID
    compiled_plan_id: UUID
    owner_scope: OwnerScope
    input_snapshot: dict[str, Any] = {}
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime


class NodeRun(BaseModel):
    node_run_id: UUID
    run_id: UUID
    node_instance_id: str
    node_type_id: str
    status: NodeRunStatus = NodeRunStatus.PENDING


class NodeRunAttempt(BaseModel):
    attempt_id: UUID
    node_run_id: UUID
    attempt_number: int = 1
    execution_epoch: int = 1
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    fixed_input: dict[str, Any] = {}
    status: AttemptStatus = AttemptStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ProviderInvocationAttempt(BaseModel):
    provider_attempt_id: UUID
    node_run_attempt_id: UUID
    provider_id: str
    model_id: str
    idempotency_key: str
    request_body_hash: str
    status: AttemptStatus = AttemptStatus.PENDING


class ProviderInvocationRecord(BaseModel):
    record_id: UUID
    provider_attempt_id: UUID
    provider_id: str
    model_id: str
    model_version: str
    idempotency_key: str
    request_body_hash: str
    response_fingerprint: str
    usage: dict[str, Any] = {}
    actual_cost: float = 0.0
    started_at: datetime
    completed_at: datetime


class ProviderOutputBinding(BaseModel):
    binding_id: UUID
    record_id: UUID
    output_artifact_version_id: UUID
    output_index: int = 0
    output_label: str = ""


class WorkflowTaskBinding(BaseModel):
    binding_id: UUID
    node_run_attempt_id: UUID
    provider_attempt_id: UUID
    provider_task_id: str
    task_status: str = "pending"


class OutboxEvent(BaseModel):
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, Any] = {}
    purpose: str  # "provider_dispatch" | "result_publish" | "notification"
    created_at: datetime
    published_at: datetime | None = None
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Human Task
# ---------------------------------------------------------------------------

class HumanTaskRecord(BaseModel):
    task_id: UUID
    task_kind: str  # "human_gate" | "request_input" | "workbench_task"
    owner_layer: str  # "workflow" | "agent"
    owner_revision_id: UUID
    run_id: UUID
    node_run_id: UUID
    attempt_id: UUID
    input_snapshot_refs: list[ArtifactRef | ResourceRef] = []
    assignee_scope: OwnerScope | None = None
    policy_strength: HumanGateStrength = HumanGateStrength.ADVISORY
    schema_ref: str = ""
    timeout_policy: dict[str, Any] = {}
    status: HumanTaskStatus = HumanTaskStatus.PENDING
    task_version: int = 1
    created_at: datetime


class HumanTaskDecision(BaseModel):
    decision_id: UUID
    task_id: UUID
    actor_id: UUID
    decision: str  # "accept" | "reject" | "revise"
    selected_refs: list[ArtifactRef | ResourceRef] = []
    typed_input: dict[str, Any] = {}
    notes: str = ""
    idempotency_key: str = ""
    created_at: datetime


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class ProviderModelRef(BaseModel):
    provider_id: str
    model_id: str
    model_version: str
    capability_snapshot_id: str = ""


class ProviderCompilationReport(BaseModel):
    report_id: UUID
    workflow_revision_id: UUID
    provider_refs: list[ProviderModelRef] = []
    control_results: list[ControlLayerResult_Model] = []
    capability_warnings: list[str] = []
    blocked_controls: list[str] = []


class ControlLayerResult_Model(BaseModel):
    layer_type: str
    control_id: str
    target_shot_id: str
    result: str  # applied|transformed|degraded|ignored_with_warning|blocked
    reason: str = ""


class CredentialBinding(BaseModel):
    binding_id: UUID
    owner_scope: OwnerScope
    tool_revision_id: UUID
    scopes: list[str] = []
    status: CredentialStatus = CredentialStatus.ACTIVE
    expires_at: datetime | None = None
    # Secret content is NOT included in this model


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project(BaseModel):
    project_id: UUID
    owner_scope: OwnerScope
    name: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    default_entry: str = ""
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AgentInvoke(BaseModel):
    agent_revision_id: UUID
    typed_inputs: dict[str, Any] = {}
    config: dict[str, Any] = {}


class AgentRevision(BaseModel):
    revision_id: UUID
    agent_kind: str  # "managed_preset" | "configurable"
    input_schema_ref: str = ""
    output_schema_ref: str = ""
    sop_steps: list[SopStep] = []
    skill_revision_refs: list[UUID] = []
    tool_revision_refs: list[UUID] = []
    execution_policy: dict[str, Any] = {}


class SopStep(BaseModel):
    step_id: str
    instruction: str
    input_bindings: dict[str, str] = {}
    output_schema_ref: str = ""
    retry_policy: dict[str, Any] = {}
    checkpoint_policy: dict[str, Any] = {}


class ToolInvocationRecord(BaseModel):
    invocation_id: UUID
    tool_revision_id: UUID
    operation_id: str
    agent_revision_id: UUID
    node_run_attempt_id: UUID
    credential_binding_id: UUID
    input_fingerprint: str = ""
    disclosure_manifest_ref: str = ""
    result_ref: str = ""
    decision_refs: list[str] = []
    usage: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

class SkillContent(BaseModel):
    purpose: str = ""
    instructions: list[str] = []
    examples: list[dict[str, Any]] = []
    knowledge_refs: list[ArtifactRef | ResourceRef] = []
    applicable_agent_roles: list[str] = []
    assembly_policy: dict[str, Any] = {}
    evaluation_notes: list[str] = []


class SkillAssemblyPlan(BaseModel):
    agent_revision_id: UUID
    skill_refs: list[UUID] = []
    resolved_sections: list[dict[str, Any]] = []
    token_accounting: dict[str, int] = {}
    conflicts: list[str] = []
    security_decisions: list[str] = []
    final_context_hash: str = ""


# ---------------------------------------------------------------------------
# Media Recipe
# ---------------------------------------------------------------------------

class MediaRecipeInvoke(BaseModel):
    media_recipe_revision_id: UUID
    typed_inputs: dict[str, Any] = {}
    config: dict[str, Any] = {}


class MediaRecipeRevision(BaseModel):
    revision_id: UUID
    operator_graph: dict[str, Any] = {}
    public_input_schema_refs: list[str] = []
    public_output_schema_refs: list[str] = []
    parameter_schema: dict[str, Any] = {}
    capability_requirements: list[str] = []


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------

class RequirementRecord(BaseModel):
    requirement_id: str
    title: str
    status: RequirementStatus = RequirementStatus.DEFINED
    target_version: str = ""
    priority: str = "P0"
    location: str = ""
    dependencies: list[str] = []
    domain: str = ""
    personal_dri: str = ""
    evidence_links: list[EvidenceLink] = []


class ChangeRequest(BaseModel):
    change_id: UUID
    proposer: str
    reason: str
    affected_ids: list[str] = []
    impact_analysis: str = ""
    decision: str = ""  # "approved" | "rejected" | "deferred"
    decision_by: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class EvidenceLink(BaseModel):
    type: EvidenceType
    url: str
    description: str = ""


class ReleaseGateSnapshot(BaseModel):
    snapshot_id: UUID
    version: str
    blocked_items: list[RequirementRecord] = []
    verified_items: list[RequirementRecord] = []
    created_at: datetime


class ThirdPartyComponent(BaseModel):
    component_id: UUID
    name: str
    repository_url: str
    commit_sha: str
    license_hash: str
    decision: GovernanceDecision = GovernanceDecision.CANDIDATE
    decision_evidence: str = ""
    notify_obligations: str = ""
    created_at: datetime


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

class EvaluationDatasetRevision(BaseModel):
    dataset_id: UUID
    version: int
    name: str
    category: str  # "text" | "identity" | "camera" | "51shots" | "ad" | "interaction"
    sample_count: int = 0
    content_hash: str = ""
    created_at: datetime


class QualityGateDecision(BaseModel):
    decision_id: UUID
    dataset_revision_id: UUID
    rubric_revision_id: UUID
    passed: bool = False
    critical_failures: list[str] = []
    summary: dict[str, Any] = {}
    decided_by: str = ""
    created_at: datetime
