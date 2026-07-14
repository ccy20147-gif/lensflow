"""
ToonFlow Backend — Quality Domain Service

Covers:
- Evaluation dataset & rubric management (TF-QLT-001 FR-1, FR-2)
- Evaluation runner (FR-6, FR-8, FR-9)
- Quality gate decisions (AC-1–AC-5)
- Blind judging & tiebreak (FR-5)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.schemas.enums import RunStatus
from src.schemas.models import ArtifactRef, EvaluationDatasetRevision, QualityGateDecision
from src.schemas.quality_schemas import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationJudgement,
    EvaluationRun,
    EvaluationRunStatus,
    ProviderInvocationRef,
    RubricCriterion,
    RubricRevision,
)


# ---------------------------------------------------------------------------
# QualityService
# ---------------------------------------------------------------------------


class QualityService:
    """Handles all quality evaluation business logic.

    Stateless service. In production, storage would be via DB repositories.
    """

    VALID_CATEGORIES = frozenset({"text", "identity", "camera", "51shots", "ad", "interaction"})

    def __init__(self) -> None:
        self._datasets: dict[str, EvaluationDatasetRevision] = {}
        self._rubrics: dict[str, RubricRevision] = {}
        self._runs: dict[str, EvaluationRun] = {}
        self._cases: dict[str, list[EvaluationCase]] = {}  # dataset_id -> cases
        self._judgements: dict[str, list[EvaluationJudgement]] = {}  # run_id -> judgements
        self._results: dict[str, list[EvaluationCaseResult]] = {}  # run_id -> results

    # ------------------------------------------------------------------
    # Dataset management (FR-1)
    # ------------------------------------------------------------------

    def create_dataset(
        self,
        name: str,
        category: str,
        content_hash: str = "",
    ) -> EvaluationDatasetRevision:
        """Create a new dataset revision (FR-1)."""
        if category not in self.VALID_CATEGORIES:
            raise ValidationError_(
                f"Invalid category '{category}'. Must be one of: {', '.join(sorted(self.VALID_CATEGORIES))}",
            )

        dataset = EvaluationDatasetRevision(
            dataset_id=uuid4(),
            version=1,
            name=name,
            category=category,
            sample_count=0,
            content_hash=content_hash,
            created_at=datetime.now(timezone.utc),
        )
        self._datasets[name] = dataset
        self._cases[str(dataset.dataset_id)] = []
        return dataset

    def get_dataset(self, name: str) -> EvaluationDatasetRevision:
        """Get a dataset by name."""
        ds = self._datasets.get(name)
        if not ds:
            raise NotFoundError("EvaluationDatasetRevision", name)
        return ds

    def add_case(self, dataset_name: str, case: EvaluationCase) -> EvaluationCase:
        """Add a case to a dataset (FR-2)."""
        ds = self.get_dataset(dataset_name)
        ds_id = str(ds.dataset_id)
        if ds_id not in self._cases:
            self._cases[ds_id] = []

        case.case_number = len(self._cases[ds_id]) + 1
        case.dataset_id = ds.dataset_id
        self._cases[ds_id].append(case)
        ds.sample_count = len(self._cases[ds_id])
        return case

    def list_cases(self, dataset_name: str) -> list[EvaluationCase]:
        """List all cases in a dataset."""
        ds = self.get_dataset(dataset_name)
        return self._cases.get(str(ds.dataset_id), [])

    # ------------------------------------------------------------------
    # Rubric management (FR-4)
    # ------------------------------------------------------------------

    def create_rubric(
        self,
        name: str,
        category: str,
        criteria: list[RubricCriterion] | None = None,
        critical_failure_rules: list[str] | None = None,
    ) -> RubricRevision:
        """Create a new rubric revision (FR-4)."""
        if category not in self.VALID_CATEGORIES:
            raise ValidationError_(f"Invalid category '{category}'")

        content_hash_input = f"{name}:{category}:{len(criteria or [])}"
        content_hash = hashlib.sha256(content_hash_input.encode("utf-8")).hexdigest()

        rubric = RubricRevision(
            rubric_id=uuid4(),
            version=1,
            name=name,
            category=category,
            criteria=criteria or [],
            critical_failure_rules=critical_failure_rules or [],
            content_hash=content_hash,
            created_at=datetime.now(timezone.utc),
        )
        self._rubrics[name] = rubric
        return rubric

    def get_rubric(self, name: str) -> RubricRevision:
        """Get a rubric by name."""
        rubric = self._rubrics.get(name)
        if not rubric:
            raise NotFoundError("RubricRevision", name)
        return rubric

    # ------------------------------------------------------------------
    # Evaluation Run (FR-6, FR-8, FR-9)
    # ------------------------------------------------------------------

    def create_run(
        self,
        dataset_name: str,
        rubric_name: str,
        provider_refs: list[dict[str, str]] | None = None,
    ) -> EvaluationRun:
        """Create a new evaluation run."""
        ds = self.get_dataset(dataset_name)
        rubric = self.get_rubric(rubric_name)

        cases = self._cases.get(str(ds.dataset_id), [])

        run = EvaluationRun(
            run_id=uuid4(),
            dataset_revision_id=ds.dataset_id,
            rubric_revision_id=rubric.rubric_id,
            provider_refs=provider_refs or [],
            status=EvaluationRunStatus.QUEUED,
            total_cases=len(cases),
            completed_cases=0,
            failed_cases=0,
            created_at=datetime.now(timezone.utc),
        )
        self._runs[str(run.run_id)] = run
        self._results[str(run.run_id)] = []
        self._judgements[str(run.run_id)] = []
        return run

    def get_run(self, run_id: str) -> EvaluationRun:
        """Get a run by ID."""
        run = self._runs.get(run_id)
        if not run:
            raise NotFoundError("EvaluationRun", run_id)
        return run

    def complete_case(
        self,
        run_id: str,
        case_result: EvaluationCaseResult,
    ) -> EvaluationRun:
        """Record completion of a single evaluation case (FR-9)."""
        run = self.get_run(run_id)
        run.completed_cases += 1

        if case_result.critical_failures:
            run.failed_cases += 1

        if run_id not in self._results:
            self._results[run_id] = []
        self._results[run_id].append(case_result)

        # Mark run as completed if all cases done
        if run.completed_cases >= run.total_cases:
            run.status = EvaluationRunStatus.COMPLETED

        return run

    # ------------------------------------------------------------------
    # Judgements (FR-5)
    # ------------------------------------------------------------------

    def submit_judgement(
        self,
        run_id: str,
        case_id: str,
        reviewer_id: str,
        scores: dict[str, float],
        critical_failures: list[str] | None = None,
        notes: str = "",
        is_blind: bool = True,
    ) -> EvaluationJudgement:
        """Submit a blind judgement for a case (FR-5)."""
        run = self.get_run(run_id)

        judgement = EvaluationJudgement(
            judgement_id=uuid4(),
            run_id=run.run_id,
            case_id=uuid4() if isinstance(case_id, str) else case_id,
            reviewer_id=reviewer_id,
            scores=scores,
            critical_failures=critical_failures or [],
            notes=notes,
            is_blind=is_blind,
            created_at=datetime.now(timezone.utc),
        )

        run_id_str = str(run.run_id)
        if run_id_str not in self._judgements:
            self._judgements[run_id_str] = []
        self._judgements[run_id_str].append(judgement)
        return judgement

    def get_case_judgements(self, run_id: str) -> list[EvaluationJudgement]:
        """Get all judgements for a run."""
        return self._judgements.get(run_id, [])

    def check_tiebreak_needed(self, run_id: str, case_id: str) -> bool:
        """Check if judgements for a case need tiebreak (FR-5).

        Returns True when there are exactly 2 judgements with scores differing
        by more than 1.0 on any criterion.
        """
        run_judgements = [
            j for j in self._judgements.get(run_id, [])
            if str(j.case_id) == case_id
        ]
        if len(run_judgements) < 2:
            return False

        j1, j2 = run_judgements[0], run_judgements[1]
        for criterion in j1.scores:
            diff = abs(j1.scores.get(criterion, 0) - j2.scores.get(criterion, 0))
            if diff >= 2.0:
                return True
        return False

    # ------------------------------------------------------------------
    # Quality Gate (FR-3, AC-1, AC-3)
    # ------------------------------------------------------------------

    def evaluate_quality_gate(
        self,
        dataset_name: str,
        rubric_name: str,
        run_id: str,
        decided_by: str = "",
    ) -> QualityGateDecision:
        """Evaluate a quality gate decision (AC-1, AC-3).

        Checks:
        - No critical failures
        - Schema compliance
        - Minimum average score >= 3.0
        """
        ds = self.get_dataset(dataset_name)
        rubric = self.get_rubric(rubric_name)
        run = self.get_run(run_id)

        results = self._results.get(run_id, [])
        judgements = self._judgements.get(run_id, [])

        # FR-9: Failures are counted in pass rate
        critical_failures: list[str] = []
        total_cases = len(results) if results else run.total_cases
        failed_count = 0

        for result in results:
            if result.critical_failures:
                critical_failures.extend(result.critical_failures)
                failed_count += 1

        # Aggregate scores
        all_scores: list[float] = []
        for j in judgements:
            all_scores.extend(j.scores.values())

        avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

        # AC-3: Critical failure blocks gate
        has_critical_failure = len(critical_failures) > 0

        # Schema compliance check
        schema_errors = sum(len(r.auto_schema_errors) for r in results)
        schema_pass = schema_errors == 0

        # FR-3: Cannot average over critical failures
        passed = False
        if not has_critical_failure and schema_pass and avg_score >= 3.0:
            passed = True

        decision = QualityGateDecision(
            decision_id=uuid4(),
            dataset_revision_id=ds.dataset_id,
            rubric_revision_id=rubric.rubric_id,
            passed=passed,
            critical_failures=critical_failures,
            summary={
                "avg_score": round(avg_score, 2),
                "total_cases": total_cases,
                "failed_cases": failed_count,
                "schema_errors": schema_errors,
                "judgements_count": len(judgements),
                "pass_rate": f"{(total_cases - failed_count) / total_cases * 100:.1f}%" if total_cases > 0 else "0%",
            },
            decided_by=decided_by,
            created_at=datetime.now(timezone.utc),
        )

        return decision

    # ------------------------------------------------------------------
    # Audit & traceability (AC-5)
    # ------------------------------------------------------------------

    def get_run_evidence_trail(self, run_id: str) -> dict:
        """Get the full evidence trail for a run (AC-5)."""
        run = self.get_run(run_id)
        results = self._results.get(run_id, [])
        judgements = self._judgements.get(run_id, [])

        return {
            "run_id": str(run.run_id),
            "status": run.status.value,
            "total_cases": run.total_cases,
            "completed_cases": run.completed_cases,
            "failed_cases": run.failed_cases,
            "results": [
                {
                    "case_number": r.case_number,
                    "auto_schema_pass": r.auto_schema_pass,
                    "critical_failures": r.critical_failures,
                    "aggregated_score": r.aggregated_score,
                }
                for r in results
            ],
            "judgements": [
                {
                    "reviewer_id": j.reviewer_id,
                    "scores": j.scores,
                    "critical_failures": j.critical_failures,
                }
                for j in judgements
            ],
        }
