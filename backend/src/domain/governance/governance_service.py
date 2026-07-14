"""
ToonFlow Backend — Governance Domain Service

Covers:
- Component registration & lifecycle (TF-GOV-002 FR-1–FR-10)
- NOTICE file generation
- Change request flow (TF-GOV-001 FR-4–FR-6)
- Requirement validation & release gate checks
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.schemas.enums import EvidenceType, GovernanceDecision, RequirementStatus
from src.schemas.models import (
    ChangeRequest,
    EvidenceLink,
    RequirementRecord,
    ThirdPartyComponent,
)


# ---------------------------------------------------------------------------
# GovernanceService
# ---------------------------------------------------------------------------


class GovernanceService:
    """Handles all governance-related business logic.

    This is a stateless service. In production, storage would be via DB
    repositories; here we demonstrate the validation rules and state machines
    as per TF-GOV-001 and TF-GOV-002.
    """

    # ------------------------------------------------------------------
    # Requirement lifecycle (TF-GOV-001)
    # ------------------------------------------------------------------

    VALID_TRANSITIONS: dict[RequirementStatus, set[RequirementStatus]] = {
        RequirementStatus.DISCOVERED: {RequirementStatus.DEFINED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.DEFINED: {RequirementStatus.REVIEWED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.REVIEWED: {RequirementStatus.APPROVED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.APPROVED: {RequirementStatus.IN_DELIVERY, RequirementStatus.DEFERRED, RequirementStatus.SUPERSEDED, RequirementStatus.REJECTED},
        RequirementStatus.IN_DELIVERY: {RequirementStatus.IMPLEMENTED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.IMPLEMENTED: {RequirementStatus.VERIFIED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.VERIFIED: {RequirementStatus.RELEASED, RequirementStatus.DEFERRED, RequirementStatus.REJECTED},
        RequirementStatus.RELEASED: {RequirementStatus.DEFERRED, RequirementStatus.SUPERSEDED},
        RequirementStatus.DEFERRED: {RequirementStatus.DISCOVERED, RequirementStatus.DEFERRED},
        RequirementStatus.SUPERSEDED: set(),
        RequirementStatus.REJECTED: set(),
    }

    def __init__(self) -> None:
        self._requirements: dict[str, RequirementRecord] = {}
        self._change_requests: dict[str, ChangeRequest] = {}
        self._components: dict[str, ThirdPartyComponent] = {}

    # -- Requirement CRUD -------------------------------------------------

    def register_requirement(
        self,
        requirement_id: str,
        title: str,
        target_version: str = "",
        priority: str = "P0",
        location: str = "",
        dependencies: list[str] | None = None,
        domain: str = "",
        personal_dri: str = "",
        detailed_doc_url: str = "",
    ) -> RequirementRecord:
        """Register a new requirement (FR-1, FR-2)."""
        if not requirement_id or not requirement_id.strip():
            raise ValidationError_("Requirement ID must not be empty")

        if requirement_id in self._requirements:
            raise ConflictError(f"Requirement ID '{requirement_id}' already exists")

        rec = RequirementRecord(
            requirement_id=requirement_id,
            title=title,
            status=RequirementStatus.DISCOVERED,
            target_version=target_version,
            priority=priority,
            location=location,
            dependencies=dependencies or [],
            domain=domain,
            personal_dri=personal_dri,
            evidence_links=[],
        )

        # FR-3: If detailed doc URL provided, attach as evidence
        if detailed_doc_url:
            rec.evidence_links.append(
                EvidenceLink(
                    type=EvidenceType.DETAILED_REQUIREMENT,
                    url=detailed_doc_url,
                    description="Detailed requirement document",
                )
            )

        self._requirements[requirement_id] = rec
        return rec

    def get_requirement(self, requirement_id: str) -> RequirementRecord:
        """Get a requirement by ID (AC-1)."""
        rec = self._requirements.get(requirement_id)
        if not rec:
            raise NotFoundError("RequirementRecord", requirement_id)
        return rec

    def list_requirements(self, domain: str | None = None) -> list[RequirementRecord]:
        """List all requirements, optionally filtered by domain."""
        if domain:
            return [r for r in self._requirements.values() if r.domain == domain]
        return list(self._requirements.values())

    def transition_requirement(
        self,
        requirement_id: str,
        new_status: RequirementStatus,
        reason: str = "",
    ) -> RequirementRecord:
        """Transition a requirement to a new status (FR-7, FR-8)."""
        rec = self.get_requirement(requirement_id)

        if new_status not in self.VALID_TRANSITIONS.get(rec.status, set()):
            raise ValidationError_(
                f"Cannot transition from '{rec.status.value}' to '{new_status.value}'",
                details={"requirement_id": requirement_id, "current_status": rec.status.value, "requested_status": new_status.value},
            )

        # FR-3: Cannot go to approved without detailed document
        if new_status == RequirementStatus.APPROVED:
            has_detail = any(
                link.type == EvidenceType.DETAILED_REQUIREMENT
                for link in rec.evidence_links
            )
            if not has_detail:
                raise ValidationError_(
                    f"Requirement '{requirement_id}' cannot be approved without a detailed document (FR-3)",
                )

        # Validate dependencies exist when transitioning to reviewed or beyond
        if new_status in {RequirementStatus.REVIEWED, RequirementStatus.APPROVED, RequirementStatus.IN_DELIVERY}:
            for dep_id in rec.dependencies:
                if dep_id not in self._requirements:
                    raise ValidationError_(
                        f"Unknown dependency '{dep_id}' on requirement '{requirement_id}'",
                    )

        rec.status = new_status
        return rec

    # -- Change Request (TF-GOV-001 FR-4, FR-5, FR-6) --------------------

    def create_change_request(
        self,
        proposer: str,
        reason: str,
        affected_ids: list[str] | None = None,
        impact_analysis: str = "",
    ) -> ChangeRequest:
        """Create a change request (FR-4)."""
        if not proposer:
            raise ValidationError_("Proposer must not be empty")
        if not reason:
            raise ValidationError_("Reason must not be empty")

        affected = affected_ids or []

        # FR-5: For public-contract changes, list transitive dependencies
        transitive_deps: set[str] = set()
        for aid in affected:
            if aid in self._requirements:
                transitive_deps.update(self._requirements[aid].dependencies)
        transitive_deps.difference_update(affected)

        cr = ChangeRequest(
            change_id=uuid4(),
            proposer=proposer,
            reason=reason,
            affected_ids=affected,
            impact_analysis=impact_analysis + (f"\nTransitive dependencies: {sorted(transitive_deps)}" if transitive_deps else ""),
            decision="",
            decision_by="",
            created_at=datetime.now(timezone.utc),
        )
        self._change_requests[str(cr.change_id)] = cr
        return cr

    def approve_change_request(
        self,
        change_id: str,
        decision: str,
        decision_by: str,
    ) -> ChangeRequest:
        """Approve, reject, or defer a change request."""
        cr = self._change_requests.get(change_id)
        if not cr:
            raise NotFoundError("ChangeRequest", change_id)

        if cr.decision:
            raise ConflictError(f"Change request {change_id} already has a final decision")

        if decision not in {"approved", "rejected", "deferred"}:
            raise ValidationError_(f"Invalid decision '{decision}'")

        cr.decision = decision
        cr.decision_by = decision_by
        return cr

    # ------------------------------------------------------------------
    # Component registration & NOTICE (TF-GOV-002)
    # ------------------------------------------------------------------

    VALID_COMPONENT_DECISIONS = {
        GovernanceDecision.CANDIDATE,
        GovernanceDecision.UNDER_REVIEW,
        GovernanceDecision.APPROVED_REUSE,
        GovernanceDecision.CLEAN_ROOM_REWRITE,
        GovernanceDecision.ABANDONED,
        GovernanceDecision.BLOCKED,
    }

    COMPONENT_LIFECYCLE: dict[GovernanceDecision, set[GovernanceDecision]] = {
        GovernanceDecision.CANDIDATE: {GovernanceDecision.UNDER_REVIEW, GovernanceDecision.ABANDONED, GovernanceDecision.BLOCKED},
        GovernanceDecision.UNDER_REVIEW: {GovernanceDecision.APPROVED_REUSE, GovernanceDecision.CLEAN_ROOM_REWRITE, GovernanceDecision.ABANDONED, GovernanceDecision.BLOCKED},
        GovernanceDecision.APPROVED_REUSE: {GovernanceDecision.APPROVED_REUSE, GovernanceDecision.UNDER_REVIEW},
        GovernanceDecision.CLEAN_ROOM_REWRITE: {GovernanceDecision.CLEAN_ROOM_REWRITE, GovernanceDecision.UNDER_REVIEW},
        GovernanceDecision.ABANDONED: {GovernanceDecision.UNDER_REVIEW, GovernanceDecision.CANDIDATE},
        GovernanceDecision.BLOCKED: {GovernanceDecision.UNDER_REVIEW},
    }

    def register_component(
        self,
        name: str,
        repository_url: str,
        commit_sha: str,
        license_text: str,
        decision: GovernanceDecision = GovernanceDecision.CANDIDATE,
        decision_evidence: str = "",
        notify_obligations: str = "",
    ) -> ThirdPartyComponent:
        """Register a third-party component (TF-GOV-002 FR-1, FR-2)."""
        if not name:
            raise ValidationError_("Component name must not be empty")
        if not commit_sha:
            raise ValidationError_("Commit SHA must not be empty")

        license_hash = hashlib.sha256(license_text.encode("utf-8")).hexdigest()

        component = ThirdPartyComponent(
            component_id=uuid4(),
            name=name,
            repository_url=repository_url,
            commit_sha=commit_sha,
            license_hash=license_hash,
            decision=decision,
            decision_evidence=decision_evidence,
            notify_obligations=notify_obligations,
            created_at=datetime.now(timezone.utc),
        )
        self._components[name] = component
        return component

    def get_component(self, name: str) -> ThirdPartyComponent:
        """Get a component by name."""
        comp = self._components.get(name)
        if not comp:
            raise NotFoundError("ThirdPartyComponent", name)
        return comp

    def list_components(self, decision: GovernanceDecision | None = None) -> list[ThirdPartyComponent]:
        """List all components, optionally filtered by decision."""
        if decision:
            return [c for c in self._components.values() if c.decision == decision]
        return list(self._components.values())

    def update_component_decision(
        self,
        name: str,
        new_decision: GovernanceDecision,
        new_evidence: str = "",
        new_notify_obligations: str = "",
    ) -> ThirdPartyComponent:
        """Update a component's reuse decision (TF-GOV-002 FR-3, FR-4, FR-5)."""
        comp = self.get_component(name)

        allowed = self.COMPONENT_LIFECYCLE.get(comp.decision, set())
        if new_decision not in allowed:
            raise ValidationError_(
                f"Cannot transition component '{name}' from '{comp.decision.value}' to '{new_decision.value}'",
            )

        comp.decision = new_decision
        if new_evidence:
            comp.decision_evidence = new_evidence
        if new_notify_obligations:
            comp.notify_obligations = new_notify_obligations
        return comp

    # -- Notice Generation (TF-GOV-002 FR-10) --------------------------

    def generate_notice_text(self) -> str:
        """Generate NOTICE file content from approved_reuse components."""
        reuse_components = [
            c for c in self._components.values()
            if c.decision == GovernanceDecision.APPROVED_REUSE
        ]

        if not reuse_components:
            return "No third-party components with distribution obligations.\n"

        lines = [
            "ToonFlow — Third-Party Notices",
            "=" * 40,
            "",
        ]
        for comp in sorted(reuse_components, key=lambda x: x.name):
            lines.append(f"Component: {comp.name}")
            lines.append(f"  Repository: {comp.repository_url}")
            lines.append(f"  License Hash: {comp.license_hash}")
            lines.append(f"  Obligations: {comp.notify_obligations or 'None'}")
            lines.append("")

        return "\n".join(lines)

    # -- Release Gate (TF-GOV-001 FR-8, AC-3) -------------------------

    def check_release_gate(self, target_version: str) -> dict:
        """Check release gate readiness for a target version (FR-8, AC-3)."""
        blocked_items: list[dict] = []
        unreviewed_components: list[dict] = []

        # Check all requirements for this version
        for req in self._requirements.values():
            if req.target_version != target_version:
                continue

            issues = []

            # AC-3: status mismatch with detailed document
            if req.status == RequirementStatus.RELEASED:
                has_detail = any(
                    link.type == EvidenceType.DETAILED_REQUIREMENT
                    for link in req.evidence_links
                )
                if not has_detail:
                    issues.append("marked released but missing detailed document")

            if issues:
                blocked_items.append({
                    "requirement_id": req.requirement_id,
                    "title": req.title,
                    "status": req.status.value,
                    "issues": issues,
                })

        # Check components: any unreviewed?
        for comp in self._components.values():
            if comp.decision == GovernanceDecision.CANDIDATE:
                unreviewed_components.append({
                    "name": comp.name,
                    "decision": comp.decision.value,
                })

        return {
            "version": target_version,
            "blocked_items": blocked_items,
            "unreviewed_components": unreviewed_components,
            "is_blocked": len(blocked_items) > 0 or len(unreviewed_components) > 0,
        }

    # -- Validation helpers ---------------------------------------------

    def validate_requirement_integrity(self, requirement_id: str) -> list[str]:
        """Run integrity checks on a requirement (FR-10, AC-3)."""
        issues: list[str] = []
        req = self._requirements.get(requirement_id)
        if not req:
            return [f"Requirement '{requirement_id}' not found"]

        # Check for unknown dependencies
        for dep in req.dependencies:
            if dep not in self._requirements:
                issues.append(f"Unknown dependency '{dep}'")

        # Check for self-dependency
        if requirement_id in req.dependencies:
            issues.append("Self-dependency detected")

        return issues
