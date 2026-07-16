"""
ToonFlow Backend — Common Pydantic Models
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .enums import (
    AccountStatus,
    AttemptStatus,
    BlobStatus,
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
    UploadSessionStatus,
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
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Content & Artifacts
# ---------------------------------------------------------------------------

class ArtifactRef(BaseModel):
    artifact_id: UUID
    artifact_version_id: UUID
    schema_id: str
    schema_version: int
    # The canonical owner scope is recorded alongside the version id so
    # cross-owner consumers can detect a forged same-owner ArtifactRef at
    # compile/read time without an extra lookup.  It is optional in the
    # Pydantic shape for backwards-compatible payloads, but the public
    # resolve path always rejects cross-owner refs regardless of whether
    # the caller declared it.
    owner_scope: OwnerScope | None = None


class LineageEdge(BaseModel):
    """Per-edge immutable provenance used to build lineage graphs.

    Order is significant: consumers replay edges by ascending ``order``.
    ``role`` distinguishes primary inputs from derived sources (for example
    ``reference`` vs ``mask`` for a generation ArtifactVersion).
    """

    source_ref: dict[str, Any]
    role: str = "input"
    order: int = 0
    producer: dict[str, Any] = {}
    transformation: dict[str, Any] = {}
    captured_policy_refs: list[str] = []


class ArtifactVersion(BaseModel):
    artifact_id: UUID
    artifact_version_id: UUID
    schema_id: str
    schema_version: int
    owner_scope: OwnerScope
    content_uri: str | None = None
    content_json: dict[str, Any] | None = None
    created_by_run_id: UUID | None = None
    # Artifact lineage also records typed execution provenance such as a
    # NodeRunAttempt, MapItemRun, or ToolInvocation.  Only entries containing
    # the complete ArtifactRef shape are artifact dependencies; callers must
    # not assume every immutable lineage edge is dereferenceable as one.
    lineage_input_refs: list[ArtifactRef | dict[str, Any]] = []
    created_at: datetime | None = None
    content_hash: str | None = None


class Resource(BaseModel):
    resource_id: UUID
    resource_type: str
    owner_scope: OwnerScope
    source_world_revision_id: UUID | None = None
    source_local_id: str | None = None
    # SHA-256 of the exact immutable embedded World character payload from
    # which this Resource was elevated.  This is distinct from the World
    # artifact/blob hash and makes the promotion independently auditable.
    source_content_hash: str | None = None
    elevation_event_id: UUID | None = None
    # Immutable promotion provenance recorded in the same transaction as
    # the Resource row.  ``bootstrap`` resources have no upstream source;
    # ``output_binding`` / ``selection_record`` carry the durable id.
    promotion_source_kind: str = "bootstrap"
    promotion_source_ref_id: UUID | None = None
    promotion_source_artifact_version_id: UUID | None = None
    created_at: datetime | None = None


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
    updated_at: datetime | None = None


class ResourceRevision(BaseModel):
    resource_id: UUID
    revision_id: UUID
    revision_number: int
    content_artifact_version_id: UUID
    revision_status: RevisionStatus = RevisionStatus.ACTIVE
    created_from_artifact_version_id: UUID | None = None
    source_world_revision_id: UUID | None = None
    source_local_id: str | None = None
    source_content_hash: str | None = None
    elevation_event_id: UUID | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Blob / Storage (TF-OPS-003)
# ---------------------------------------------------------------------------


class BlobRef(BaseModel):
    """Public reference to a Blob.  Business code stores ``blob_id``; the
    internal storage key is never exposed through this shape.
    """

    blob_id: UUID
    owner_scope: OwnerScope
    media_type: str
    size_bytes: int
    content_hash: str
    status: BlobStatus = BlobStatus.UPLOADING
    storage_key: str = ""  # populated only for server-side lookup, never returned
    created_at: datetime | None = None


class UploadSession(BaseModel):
    session_id: UUID
    blob_id: UUID
    owner_scope: OwnerScope
    expected_size_bytes: int
    expected_content_hash: str
    idempotency_key: str
    status: UploadSessionStatus = UploadSessionStatus.INITIATED
    part_state: list[dict[str, Any]] = []
    expires_at: datetime | None = None
    created_at: datetime | None = None


class BlobReference(BaseModel):
    """Reference table entry that ties a Blob to its consumer rows.

    Every ArtifactVersion that backs onto a Blob records one row here.  The
    reference is created in the SAME transaction as the ArtifactVersion so
    deletion protection cannot lag behind canonical writes.
    """

    blob_id: UUID
    artifact_version_id: UUID
    owner_scope: OwnerScope
    role: str = "primary"  # primary | attachment | reference
    created_at: datetime | None = None


class CasConflict(BaseModel):
    """Structured conflict returned by ResourceDraft CAS or freeze.

    Both sides always carry the stable ref the client needs to recover:
    the expected base, the current persisted state and the proposed value.
    """

    resource_id: UUID
    operation: str  # "save_draft" | "freeze"
    base_draft_version: int
    current_draft_version: int
    current_content_artifact_version_id: UUID
    proposed_content_artifact_version_id: UUID
    reason: str = ""


class EntitlementDecision(BaseModel):
    """Result of re-evaluating a ResourceRef against the live grant.

    Compiled plans, runs, publishes and commercial exports must recompute
    the decision on every new action; a GrantSnapshot is evidence only.
    """

    subject_scope: OwnerScope
    resource_revision_id: UUID
    action: str
    decision: str  # "allow" | "deny"
    grant_snapshot_id: UUID | None = None
    reason: str = ""
    evaluated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Promotion gate (TF-WF-005 + TF-PLT-003)
# ---------------------------------------------------------------------------


class PromotionSource(BaseModel):
    """Identity of the durable producer for a Resource promotion.

    The Foundation contract accepts a single, non-superseded OutputBinding
    or SelectionRecord.  Calls that pass a bare ``artifact_id`` or an
    ``output_binding_id`` that points to a superseded candidate MUST be
    rejected; the resolver below enforces this.
    """

    kind: str  # "output_binding" | "selection_record"
    binding_id: UUID | None = None
    selection_id: UUID | None = None
    output_index: int | None = None
    superseded: bool = False


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class Workflow(BaseModel):
    workflow_id: UUID
    owner_scope: OwnerScope
    created_at: datetime | None = None


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
    # full_draft_hash covers every persisted draft fact including
    # ``draft_version``.  Activate/save CAS on it; ``graph_hash`` alone is
    # preserved for older clients and graph-hash-only diff views, but two
    # pure layout saves would collide on graph_hash and the contract requires
    # us to refuse the second one.
    full_draft_hash: str = ""
    updated_at: datetime | None = None


class WorkflowRevision(BaseModel):
    workflow_id: UUID
    revision_id: UUID
    revision_number: int
    graph_hash: str
    execution_hash: str
    registry_snapshot_id: UUID
    # A revision must be self-contained: compiling an active revision cannot
    # depend on whichever draft happens to exist later.
    graph: dict[str, Any] = {}
    config: dict[str, Any] = {}
    layout: dict[str, Any] = {}
    compiled_plan_ids: list[UUID] = []
    revision_status: RevisionStatus = RevisionStatus.ACTIVE
    created_at: datetime | None = None


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
    # Optional platform-owned expansion template for a managed Agent card.
    # It is part of the registry revision, never AgentRevision content.
    managed_agent_task_plan: list[dict[str, Any]] = []


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
    created_at: datetime | None = None


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
    # Structured ProviderCompilationReport persisted with the plan so the
    # Provider outcome vocabulary is auditable on replay.  ``None`` is
    # only produced by legacy test doubles that pre-date TF-WF-003 batch B.
    provider_compilation_report: ProviderCompilationReport | None = None
    # Server-derived authorization evidence used at compilation time. It is
    # intentionally decision metadata only: no credential or raw policy body.
    actor_scope: str = ""
    entitlement_snapshot: dict[str, Any] = {}
    # Frozen entitlement snapshots produced by the TF-SEC-001 minimum gate.
    # Each entry references the canonical target id and the live decision
    # made against the snapshot at compile time.
    entitlement_snapshots: list[dict[str, Any]] = []
    budget_limits: dict[str, Any] = {}
    compiler_version: str = "1.0"
    plan_hash: str = ""
    created_at: datetime | None = None


class WorkflowRun(BaseModel):
    run_id: UUID
    workflow_revision_id: UUID
    compiled_plan_id: UUID
    owner_scope: OwnerScope
    input_snapshot: dict[str, Any] = {}
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime | None = None


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
    # Foundation scope: dedupe_key pins provider_dispatch rows to the
    # invocation_attempt_id and result_publish rows to the same id so
    # a re-delivered event cannot create a second external side effect.
    dedupe_key: str | None = None
    created_at: datetime | None = None
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
    created_at: datetime | None = None


class HumanTaskDecision(BaseModel):
    decision_id: UUID
    task_id: UUID
    actor_id: UUID
    decision: str  # "accept" | "reject" | "revise"
    selected_refs: list[ArtifactRef | ResourceRef] = []
    typed_input: dict[str, Any] = {}
    notes: str = ""
    idempotency_key: str = ""
    created_at: datetime | None = None


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
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    revision_number: int = 0
    content_hash: str = ""
    revision_status: RevisionStatus = RevisionStatus.DRAFT
    input_schema_ref: str = ""
    output_schema_ref: str = ""
    # Frozen executable contract paired with output_schema_ref. The port
    # registry supplies identity/version; this guards actual provider JSON.
    output_schema: dict[str, Any] | None = None
    sop_steps: list[SopStep] = []
    # A same-owner Skill may use its frozen revision UUID.  A foreign Skill
    # must retain the exact, grant-bearing ResourceRef in the immutable Agent
    # revision so a later invocation can re-check it instead of resolving
    # "latest" or trusting a stale authorization decision.
    skill_revision_refs: list[UUID | ResourceRef] = []
    tool_revision_refs: list[UUID] = []
    # A frozen allowlist for each external side effect.  A bare ToolRevision
    # reference is deliberately insufficient at invocation time: the Agent
    # must also declare the operations, scopes, and fields it may disclose.
    tool_access_plan: list["ToolAccessPlanEntry"] = []
    execution_policy: dict[str, Any] = {}
    # These are persisted revision contracts, not UI-only preferences.  A
    # missing value on a pre-migration revision means the bounded platform
    # default (RequestInput disabled, broker-only execution).
    request_input_policy: dict[str, Any] = {}
    execution_boundary: str = "runtime_and_approved_tool_broker_only"


class SopStep(BaseModel):
    step_id: str
    instruction: str
    input_bindings: dict[str, str] = {}
    # These mappings are part of the immutable SOP contract.  They are kept
    # separate from the schema reference so Studio can describe data flow
    # without embedding executable expressions.
    output_bindings: dict[str, str] = {}
    output_schema_ref: str = ""
    retry_policy: dict[str, Any] = {}
    checkpoint_policy: dict[str, Any] = {}
    failure_policy: dict[str, Any] = {}


class ToolAccessPlanOperation(BaseModel):
    operation_id: str
    allowed_scopes: list[str] = []
    disclosure_fields: list[str] = []


class ToolAccessPlanEntry(BaseModel):
    tool_revision_id: UUID
    operations: list[ToolAccessPlanOperation] = []


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
    estimated_tokens: int = 0
    max_assembly_tokens: int = 4096
    conflict_tags: list[str] = []
    priority: int = 100
    language: str = "und"
    safety_classification: str = "standard"
    required_context_schema: str = ""
    assembly_tier: str = "explicit"


class SkillAssemblyPlan(BaseModel):
    plan_id: UUID = UUID(int=0)
    agent_revision_id: UUID
    skill_refs: list[UUID] = []
    resolved_resource_refs: list[ResourceRef] = []
    resolved_sections: list[dict[str, Any]] = []
    token_accounting: dict[str, int] = {}
    conflicts: list[str] = []
    rejected_skills: list[dict[str, str]] = []
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
    recipe_id: UUID | None = None
    revision_number: int = 0
    content_hash: str = ""
    base_hash: str | None = None
    revision_status: RevisionStatus = RevisionStatus.DRAFT
    created_at: datetime | None = None
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
    created_at: datetime | None = None
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
    created_at: datetime | None = None


class ThirdPartyComponent(BaseModel):
    component_id: UUID
    name: str
    repository_url: str
    commit_sha: str
    license_hash: str
    decision: GovernanceDecision = GovernanceDecision.CANDIDATE
    decision_evidence: str = ""
    notify_obligations: str = ""
    created_at: datetime | None = None


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
    created_at: datetime | None = None


class QualityGateDecision(BaseModel):
    decision_id: UUID
    dataset_revision_id: UUID
    rubric_revision_id: UUID
    passed: bool = False
    critical_failures: list[str] = []
    summary: dict[str, Any] = {}
    decided_by: str = ""
    created_at: datetime | None = None
