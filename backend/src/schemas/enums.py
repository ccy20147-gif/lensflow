"""
ToonFlow Backend — Canonical State Machines & Enums

All domain modules import from here — no parallel enum definitions allowed.
"""
from __future__ import annotations

from enum import StrEnum


class RequirementStatus(StrEnum):
    DISCOVERED = "discovered"
    DEFINED = "defined"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    IN_DELIVERY = "in_delivery"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    RELEASED = "released"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeRunStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AttemptStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class HumanTaskStatus(StrEnum):
    PENDING = "pending"
    WAITING = "waiting"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AccountStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETION_PENDING = "deletion_pending"
    DELETED_TOMBSTONE = "deleted_tombstone"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETION_PENDING = "deletion_pending"
    DELETED_TOMBSTONE = "deleted_tombstone"


class AgentKind(StrEnum):
    MANAGED_PRESET = "managed_preset"
    CONFIGURABLE = "configurable"


class ToolApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class CredentialStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DISABLED = "disabled"


class HumanGateStrength(StrEnum):
    ADVISORY = "advisory"
    DOMAIN_REQUIRED = "domain_required"
    POLICY_REQUIRED = "policy_required"


class WorkbenchActionType(StrEnum):
    PROVIDER_PRECOMPILE = "provider.precompile"
    BOARD_GENERATE = "board.generate"
    GRID_GENERATE = "grid.generate"
    GRID_CELL_REGENERATE = "grid.cell.regenerate"
    DIRECTOR_SCENE_EXPORT_CONTROLS = "director_scene.export_controls"
    CONTINUITY_CHECK = "continuity.check"
    SHOT_GENERATE = "shot.generate"
    SHOT_RERUN = "shot.rerun"


class WorkbenchScopeKind(StrEnum):
    PROJECT = "project"
    SEQUENCE = "sequence"
    COVERAGE = "coverage"
    SHOT = "shot"
    TEMPORAL_ANCHOR = "temporal_anchor"


class GovernanceDecision(StrEnum):
    CANDIDATE = "candidate"
    UNDER_REVIEW = "under_review"
    APPROVED_REUSE = "approved_reuse"
    CLEAN_ROOM_REWRITE = "clean_room_rewrite"
    PRIVATE_AUTHORIZED_REFERENCE = "private_authorized_reference"
    ABANDONED = "abandoned"
    BLOCKED = "blocked"


class EvidenceType(StrEnum):
    DETAILED_REQUIREMENT = "detailed_requirement"
    ADR = "ADR"
    DESIGN = "design"
    SCHEMA = "schema"
    IMPLEMENTATION = "implementation"
    AUTOMATED_TEST = "automated_test"
    E2E = "E2E"
    VISUAL_EVIDENCE = "visual_evidence"
    MONITORING = "monitoring"
    ROLLBACK = "rollback"


class DependencyKind(StrEnum):
    NODE_DEFINITION = "node_definition"
    CONVERTER = "converter"
    AGENT = "agent"
    MEDIA_RECIPE = "media_recipe"
    SKILL = "skill"
    WORKFLOW = "workflow"
    RESOURCE = "resource"
    PROVIDER = "provider"
    TEMPLATE = "template"


class ControlLayerType(StrEnum):
    CAMERA = "camera"
    LIGHTING = "lighting"
    COMPOSITION = "composition"
    POSE = "pose"
    EXPRESSION = "expression"
    MOTION = "motion"
    STYLIZATION = "stylization"


class ControlLayerResult(StrEnum):
    APPLIED = "applied"
    TRANSFORMED = "transformed"
    DEGRADED = "degraded"
    IGNORED_WITH_WARNING = "ignored_with_warning"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Control Flow — Condition / Join / ForEach / MapItem (TF-CF-001)
# ---------------------------------------------------------------------------


class ConditionOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN_OP = "in"
    CONTAINS = "contains"
    EXISTS = "exists"


class JoinStrategy(StrEnum):
    AND = "and"
    OR = "or"
    XOR = "xor"
    SEQUENTIAL = "sequential"


class ForEachMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    BATCH = "batch"


class MapItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
