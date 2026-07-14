"""
ToonFlow Backend — Quality-Specific Pydantic Models

Extended evaluation models beyond what is defined in src.schemas.models.
Covers EvaluationCase, RubricRevision, EvaluationRun, EvaluationJudgement.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.schemas.enums import RunStatus
from src.schemas.models import ArtifactRef, ProviderInvocationRecord, ResourceRef


# ---------------------------------------------------------------------------
# Quality Enums
# ---------------------------------------------------------------------------


class EvaluationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JudgementSeverity(StrEnum):
    PASS = "pass"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Rubric
# ---------------------------------------------------------------------------


class RubricCriterion(BaseModel):
    criterion_id: str
    name: str
    description: str
    weight: float = 1.0
    min_score: float = 1.0
    max_score: float = 5.0
    score_descriptions: dict[float, str] = {}


class RubricRevision(BaseModel):
    rubric_id: UUID
    version: int
    name: str
    category: str  # "text" | "identity" | "camera" | "51shots" | "ad" | "interaction"
    criteria: list[RubricCriterion] = []
    critical_failure_rules: list[str] = []
    content_hash: str = ""
    created_at: datetime


# ---------------------------------------------------------------------------
# Evaluation Case & Dataset
# ---------------------------------------------------------------------------


class EvaluationCase(BaseModel):
    case_id: UUID = Field(default_factory=uuid4)
    dataset_id: UUID
    case_number: int
    input_text: str = ""
    input_refs: list[ArtifactRef | ResourceRef] = []
    expected_schema_id: str = ""
    metadata: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Evaluation Run
# ---------------------------------------------------------------------------


class EvaluationRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    dataset_revision_id: UUID
    rubric_revision_id: UUID
    provider_refs: list[dict[str, str]] = []  # [{"provider_id": ..., "model_id": ..., "model_version": ...}]
    status: EvaluationRunStatus = EvaluationRunStatus.QUEUED
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    idempotency_keys: list[str] = []
    created_at: datetime


# ---------------------------------------------------------------------------
# Evaluation Judgement
# ---------------------------------------------------------------------------


class EvaluationJudgement(BaseModel):
    judgement_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    case_id: UUID
    reviewer_id: str = ""
    scores: dict[str, float] = {}  # criterion_id -> score
    critical_failures: list[str] = []
    notes: str = ""
    is_blind: bool = True
    created_at: datetime


class EvaluationCaseResult(BaseModel):
    case_id: UUID
    case_number: int
    input_text: str = ""
    output_refs: list[ArtifactRef] = []
    provider_record: ProviderInvocationRecord | None = None
    auto_schema_pass: bool = False
    auto_schema_errors: list[str] = []
    auto_metric_scores: dict[str, float] = {}
    critical_failures: list[str] = []
    judgements: list[EvaluationJudgement] = []
    aggregated_score: float = 0.0
    requires_tiebreak: bool = False
    tiebreak_judgement: EvaluationJudgement | None = None


# ---------------------------------------------------------------------------
# Invocation Record Ref (lightweight)
# ---------------------------------------------------------------------------


class ProviderInvocationRef(BaseModel):
    record_id: UUID
    provider_id: str
    model_id: str
    model_version: str
    actual_cost: float = 0.0
