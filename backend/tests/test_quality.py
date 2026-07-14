"""
Tests for Quality Domain — TF-QLT-001

Covers:
- Dataset & rubric management (FR-1, FR-2, FR-4)
- Evaluation run lifecycle (FR-6, FR-8, FR-9)
- Blind judging & tiebreak (FR-5)
- Quality gate decisions (FR-3, AC-1–AC-5)
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.exceptions import NotFoundError, ValidationError_
from src.domain.quality.quality_service import QualityService
from src.schemas.models import EvaluationDatasetRevision
from src.schemas.quality_schemas import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationRunStatus,
    RubricCriterion,
    RubricRevision,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> QualityService:
    return QualityService()


@pytest.fixture
def text_dataset(svc: QualityService) -> EvaluationDatasetRevision:
    """Create a text evaluation dataset with cases."""
    ds = svc.create_dataset(name="foundation-text-v1", category="text")
    svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=1, input_text="Hello world"))
    svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=2, input_text="Write a short story"))
    return ds


@pytest.fixture
def text_rubric(svc: QualityService) -> RubricRevision:
    """Create a text evaluation rubric."""
    return svc.create_rubric(
        name="foundation-text-rubric-v1",
        category="text",
        criteria=[
            RubricCriterion(criterion_id="coherence", name="Coherence", description="Text coherence and flow", min_score=1.0, max_score=5.0),
            RubricCriterion(criterion_id="relevance", name="Relevance", description="Relevance to prompt", min_score=1.0, max_score=5.0),
        ],
        critical_failure_rules=["Empty output is critical failure"],
    )


# ---------------------------------------------------------------------------
# Dataset Management — TF-QLT-001
# ---------------------------------------------------------------------------


class TestDatasetManagement:
    """FR-1, FR-2: Dataset creation and management."""

    def test_create_dataset(self, svc: QualityService) -> None:
        """FR-1: Dataset is created with correct metadata."""
        ds = svc.create_dataset(name="identity-v1", category="identity", content_hash="abc123")
        assert ds.name == "identity-v1"
        assert ds.category == "identity"
        assert ds.content_hash == "abc123"
        assert ds.version == 1
        assert ds.sample_count == 0

    def test_create_dataset_invalid_category(self, svc: QualityService) -> None:
        """FR-2: Invalid category is rejected."""
        with pytest.raises(ValidationError_, match="Invalid category"):
            svc.create_dataset(name="bad-category", category="invalid")

    def test_add_cases_to_dataset(self, svc: QualityService) -> None:
        """FR-2: Cases can be added to a dataset."""
        ds = svc.create_dataset(name="test-ds", category="text")
        case1 = svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=1, input_text="Test 1"))
        case2 = svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=2, input_text="Test 2"))
        assert case1.case_number == 1
        assert case2.case_number == 2

        # Sample count should update
        assert svc.get_dataset("test-ds").sample_count == 2

    def test_list_cases(self, svc: QualityService) -> None:
        """Cases can be listed by dataset."""
        ds = svc.create_dataset(name="list-ds", category="text")
        svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=1, input_text="A"))
        svc.add_case(ds.name, EvaluationCase(case_id=uuid4(), dataset_id=ds.dataset_id, case_number=2, input_text="B"))
        cases = svc.list_cases("list-ds")
        assert len(cases) == 2
        assert cases[0].input_text == "A"


class TestRubricManagement:
    """FR-4: Rubric creation."""

    def test_create_rubric(self, svc: QualityService) -> None:
        """FR-4: Rubric is created with criteria and content hash."""
        rubric = svc.create_rubric(
            name="identity-rubric-v1",
            category="identity",
            criteria=[
                RubricCriterion(criterion_id="facial_consistency", name="Facial Consistency", description="Face matches reference"),
            ],
            critical_failure_rules=["Face not recognizable -> CRITICAL"],
        )
        assert rubric.name == "identity-rubric-v1"
        assert rubric.category == "identity"
        assert len(rubric.criteria) == 1
        assert len(rubric.critical_failure_rules) == 1
        assert len(rubric.content_hash) == 64

    def test_create_rubric_invalid_category(self, svc: QualityService) -> None:
        """Rubric with invalid category is rejected."""
        with pytest.raises(ValidationError_):
            svc.create_rubric(name="bad", category="bad_category")

    def test_get_rubric(self, svc: QualityService) -> None:
        """Rubric can be retrieved by name."""
        svc.create_rubric(name="my-rubric", category="text")
        rubric = svc.get_rubric("my-rubric")
        assert rubric.name == "my-rubric"

    def test_get_nonexistent_rubric(self, svc: QualityService) -> None:
        """Nonexistent rubric raises NotFound."""
        with pytest.raises(NotFoundError):
            svc.get_rubric("nonexistent")


# ---------------------------------------------------------------------------
# Evaluation Run — TF-QLT-001
# ---------------------------------------------------------------------------


class TestEvaluationRun:
    """FR-6, FR-8, FR-9: Evaluation run lifecycle."""

    def test_create_run(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """FR-6: Run is created with correct references."""
        run = svc.create_run(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            provider_refs=[{"provider_id": "openai", "model_id": "gpt-4", "model_version": "latest"}],
        )
        assert run.status == EvaluationRunStatus.QUEUED
        assert run.total_cases == 2
        assert len(run.provider_refs) == 1

    def test_create_run_nonexistent_dataset(self, svc: QualityService, text_rubric) -> None:
        """Creating run with nonexistent dataset raises error."""
        with pytest.raises(NotFoundError):
            svc.create_run(dataset_name="nonexistent", rubric_name="foundation-text-rubric-v1")

    def test_complete_case(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """FR-9: Completing a case updates run progress."""
        run = svc.create_run(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
        )

        result = EvaluationCaseResult(
            case_id=uuid4(),
            case_number=1,
            input_text="Hello",
            auto_schema_pass=True,
            critical_failures=[],
        )
        updated = svc.complete_case(str(run.run_id), result)
        assert updated.completed_cases == 1
        assert updated.failed_cases == 0

    def test_failed_case_counted(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """FR-9: Failed case increments failed_cases."""
        run = svc.create_run(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
        )

        result = EvaluationCaseResult(
            case_id=uuid4(),
            case_number=1,
            input_text="Failing case",
            critical_failures=["Empty output"],
        )
        updated = svc.complete_case(str(run.run_id), result)
        assert updated.failed_cases == 1

    def test_run_completes_automatically(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """Run is marked completed when all cases are done."""
        run = svc.create_run(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
        )

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
            )
            svc.complete_case(str(run.run_id), result)

        assert svc.get_run(str(run.run_id)).status == EvaluationRunStatus.COMPLETED
        assert svc.get_run(str(run.run_id)).completed_cases == 2


# ---------------------------------------------------------------------------
# Blind Judging & Tiebreak — TF-QLT-001
# ---------------------------------------------------------------------------


class TestBlindJudging:
    """FR-5: Blind judging and tiebreak."""

    def test_submit_judgement(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """A judgement can be submitted for a case."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        case_id = str(uuid4())
        judgement = svc.submit_judgement(
            run_id=str(run.run_id),
            case_id=case_id,
            reviewer_id="reviewer-1",
            scores={"coherence": 4.0, "relevance": 5.0},
            is_blind=True,
        )
        assert judgement.reviewer_id == "reviewer-1"
        assert judgement.scores["coherence"] == 4.0
        assert judgement.is_blind is True

    def test_tiebreak_not_needed_when_close(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """No tiebreak when scores differ by ≤1."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")
        case_id = str(uuid4())

        svc.submit_judgement(str(run.run_id), case_id, "reviewer-1", {"coherence": 4.0})
        svc.submit_judgement(str(run.run_id), case_id, "reviewer-2", {"coherence": 5.0})
        assert svc.check_tiebreak_needed(str(run.run_id), case_id) is False

    def test_tiebreak_needed_when_far_apart(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """AC-4: Tiebreak triggered when scores differ by ≥2."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")
        case_id = str(uuid4())

        svc.submit_judgement(str(run.run_id), case_id, "reviewer-1", {"coherence": 5.0})
        svc.submit_judgement(str(run.run_id), case_id, "reviewer-2", {"coherence": 2.0})
        assert svc.check_tiebreak_needed(str(run.run_id), case_id) is True


# ---------------------------------------------------------------------------
# Quality Gate Decision — TF-QLT-001
# ---------------------------------------------------------------------------


class TestQualityGate:
    """FR-3, AC-1, AC-3: Quality gate decisions."""

    def test_gate_passes_clean(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """Clean run with good scores passes."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        # Complete cases
        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
            )
            svc.complete_case(str(run.run_id), result)

        # Submit good judgements
        for case in cases:
            svc.submit_judgement(str(run.run_id), str(case.case_id), "reviewer-1",
                                 {"coherence": 4.0, "relevance": 5.0})
            svc.submit_judgement(str(run.run_id), str(case.case_id), "reviewer-2",
                                 {"coherence": 4.0, "relevance": 4.0})

        decision = svc.evaluate_quality_gate(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            run_id=str(run.run_id),
            decided_by="qa-lead",
        )
        assert decision.passed is True
        assert len(decision.critical_failures) == 0
        assert decision.summary["avg_score"] >= 3.0

    def test_gate_blocked_by_critical_failure(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """AC-3: Critical failure blocks the gate regardless of average score."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
                critical_failures=["Empty output"],
            )
            svc.complete_case(str(run.run_id), result)

        # Even with high scores...
        for case in cases:
            svc.submit_judgement(str(run.run_id), str(case.case_id), "reviewer-1",
                                 {"coherence": 5.0, "relevance": 5.0})

        decision = svc.evaluate_quality_gate(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            run_id=str(run.run_id),
        )
        assert decision.passed is False
        assert "Empty output" in decision.critical_failures

    def test_gate_blocked_by_schema_errors(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """Schema errors block the quality gate."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=False,
                auto_schema_errors=["Missing required field: content"],
            )
            svc.complete_case(str(run.run_id), result)

        for case in cases:
            svc.submit_judgement(str(run.run_id), str(case.case_id), "reviewer-1",
                                 {"coherence": 4.0, "relevance": 4.0})

        decision = svc.evaluate_quality_gate(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            run_id=str(run.run_id),
        )
        assert decision.passed is False

    def test_gate_blocked_by_low_scores(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """Low average score blocks gate."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
            )
            svc.complete_case(str(run.run_id), result)

        for case in cases:
            svc.submit_judgement(str(run.run_id), str(case.case_id), "reviewer-1",
                                 {"coherence": 1.0, "relevance": 1.0})

        decision = svc.evaluate_quality_gate(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            run_id=str(run.run_id),
        )
        assert decision.passed is False
        assert decision.summary["avg_score"] < 3.0

    def test_gate_creates_detailed_summary(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """Gate decision includes detailed summary."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
            )
            svc.complete_case(str(run.run_id), result)

        decision = svc.evaluate_quality_gate(
            dataset_name="foundation-text-v1",
            rubric_name="foundation-text-rubric-v1",
            run_id=str(run.run_id),
            decided_by="qa-lead",
        )
        assert decision.summary["total_cases"] == 2
        assert decision.summary["failed_cases"] == 0
        assert "avg_score" in decision.summary
        assert "pass_rate" in decision.summary


# ---------------------------------------------------------------------------
# Audit & Traceability — TF-QLT-001
# ---------------------------------------------------------------------------


class TestEvidenceTrail:
    """AC-5: Traceability."""

    def test_evidence_trail(self, svc: QualityService, text_dataset, text_rubric) -> None:
        """AC-5: Run evidence trail is retrievable."""
        run = svc.create_run(dataset_name="foundation-text-v1", rubric_name="foundation-text-rubric-v1")

        cases = svc.list_cases("foundation-text-v1")
        for case in cases:
            result = EvaluationCaseResult(
                case_id=case.case_id,
                case_number=case.case_number,
                input_text=case.input_text,
                auto_schema_pass=True,
            )
            svc.complete_case(str(run.run_id), result)

        trail = svc.get_run_evidence_trail(str(run.run_id))
        assert trail["run_id"] == str(run.run_id)
        assert len(trail["results"]) == 2
        assert trail["status"] == EvaluationRunStatus.COMPLETED.value
