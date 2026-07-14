"""
ToonFlow Backend — Governance API Routes

Covers:
- Requirement CRUD & transitions (TF-GOV-001)
- Change request management
- Component CRUD (TF-GOV-002)
- NOTICE generation
- Release gate check
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.governance.governance_service import GovernanceService
from src.schemas.enums import GovernanceDecision, RequirementStatus
from src.schemas.models import ChangeRequest, RequirementRecord, ThirdPartyComponent

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


def get_governance_service() -> GovernanceService:
    return GovernanceService()


# ---------------------------------------------------------------------------
# Requirements (TF-GOV-001)
# ---------------------------------------------------------------------------


@router.post("/requirements", response_model=RequirementRecord)
async def create_requirement(
    requirement_id: str,
    title: str,
    target_version: str = "",
    priority: str = "P0",
    location: str = "",
    dependencies: str = "",
    domain: str = "",
    personal_dri: str = "",
    detailed_doc_url: str = "",
    svc: GovernanceService = Depends(get_governance_service),
):
    """Register a new requirement (FR-1, FR-2)."""
    dep_list = [d.strip() for d in dependencies.split(",") if d.strip()] if dependencies else []
    try:
        return svc.register_requirement(
            requirement_id=requirement_id,
            title=title,
            target_version=target_version,
            priority=priority,
            location=location,
            dependencies=dep_list,
            domain=domain,
            personal_dri=personal_dri,
            detailed_doc_url=detailed_doc_url,
        )
    except (ConflictError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/requirements/{requirement_id}", response_model=RequirementRecord)
async def get_requirement(
    requirement_id: str,
    svc: GovernanceService = Depends(get_governance_service),
):
    """Get a requirement by ID (AC-1)."""
    try:
        return svc.get_requirement(requirement_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/requirements", response_model=list[RequirementRecord])
async def list_requirements(
    domain: str | None = None,
    svc: GovernanceService = Depends(get_governance_service),
):
    """List all requirements, optionally filtered by domain."""
    return svc.list_requirements(domain=domain)


@router.post("/requirements/{requirement_id}/transition", response_model=RequirementRecord)
async def transition_requirement(
    requirement_id: str,
    new_status: RequirementStatus,
    reason: str = "",
    svc: GovernanceService = Depends(get_governance_service),
):
    """Transition a requirement to a new status."""
    try:
        return svc.transition_requirement(requirement_id, new_status, reason)
    except (NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Change Requests (TF-GOV-001 FR-4, FR-5, FR-6)
# ---------------------------------------------------------------------------


@router.post("/change-requests", response_model=ChangeRequest)
async def create_change_request(
    proposer: str,
    reason: str,
    affected_ids: str = "",
    impact_analysis: str = "",
    svc: GovernanceService = Depends(get_governance_service),
):
    """Create a change request (FR-4)."""
    affected = [a.strip() for a in affected_ids.split(",") if a.strip()] if affected_ids else []
    try:
        return svc.create_change_request(
            proposer=proposer,
            reason=reason,
            affected_ids=affected,
            impact_analysis=impact_analysis,
        )
    except ValidationError_ as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/change-requests/{change_id}/decision", response_model=ChangeRequest)
async def decide_change_request(
    change_id: str,
    decision: str,
    decision_by: str,
    svc: GovernanceService = Depends(get_governance_service),
):
    """Approve, reject, or defer a change request."""
    try:
        return svc.approve_change_request(
            change_id=change_id,
            decision=decision,
            decision_by=decision_by,
        )
    except (NotFoundError, ConflictError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Third-Party Components (TF-GOV-002)
# ---------------------------------------------------------------------------


@router.post("/components", response_model=ThirdPartyComponent)
async def register_component(
    name: str,
    repository_url: str,
    commit_sha: str,
    license_text: str,
    decision: GovernanceDecision = GovernanceDecision.CANDIDATE,
    decision_evidence: str = "",
    notify_obligations: str = "",
    svc: GovernanceService = Depends(get_governance_service),
):
    """Register a third-party component (TF-GOV-002 FR-1)."""
    try:
        return svc.register_component(
            name=name,
            repository_url=repository_url,
            commit_sha=commit_sha,
            license_text=license_text,
            decision=decision,
            decision_evidence=decision_evidence,
            notify_obligations=notify_obligations,
        )
    except ValidationError_ as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/components/{name}", response_model=ThirdPartyComponent)
async def get_component(
    name: str,
    svc: GovernanceService = Depends(get_governance_service),
):
    """Get a component by name."""
    try:
        return svc.get_component(name)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/components", response_model=list[ThirdPartyComponent])
async def list_components(
    decision: GovernanceDecision | None = None,
    svc: GovernanceService = Depends(get_governance_service),
):
    """List all components, optionally filtered by decision."""
    return svc.list_components(decision=decision)


@router.patch("/components/{name}/decision", response_model=ThirdPartyComponent)
async def update_component_decision(
    name: str,
    new_decision: GovernanceDecision,
    decision_evidence: str = "",
    notify_obligations: str = "",
    svc: GovernanceService = Depends(get_governance_service),
):
    """Update component decision (TF-GOV-002 FR-3–FR-7)."""
    try:
        return svc.update_component_decision(
            name=name,
            new_decision=new_decision,
            new_evidence=decision_evidence,
            new_notify_obligations=notify_obligations,
        )
    except (NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# NOTICE Generation (TF-GOV-002 FR-10)
# ---------------------------------------------------------------------------


@router.get("/notice")
async def generate_notice(svc: GovernanceService = Depends(get_governance_service)):
    """Generate NOTICE file content from approved_reuse components."""
    return {"notice": svc.generate_notice_text()}


# ---------------------------------------------------------------------------
# Release Gate (TF-GOV-001 FR-8)
# ---------------------------------------------------------------------------


@router.get("/release-gate/{target_version}")
async def check_release_gate(
    target_version: str,
    svc: GovernanceService = Depends(get_governance_service),
):
    """Check release gate readiness."""
    return svc.check_release_gate(target_version)
