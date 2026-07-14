"""
Tests for Governance Domain — TF-GOV-001 & TF-GOV-002

Covers:
- Requirement lifecycle (FR-1 to FR-10)
- Change request flow (FR-4 to FR-6)
- Component registration & lifecycle (TF-GOV-002 FR-1 to FR-10)
- NOTICE generation (FR-10)
- Release gate checks (FR-8, AC-3)
"""
from __future__ import annotations


import pytest

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.governance.governance_service import GovernanceService
from src.schemas.enums import GovernanceDecision, RequirementStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> GovernanceService:
    return GovernanceService()


# ---------------------------------------------------------------------------
# Requirement Lifecycle — TF-GOV-001
# ---------------------------------------------------------------------------


class TestRequirementLifecycle:
    """FR-1, FR-2, FR-3: Registration, metadata, and detailed doc requirement."""

    def test_register_requirement_creates_record(self, svc: GovernanceService) -> None:
        """FR-1: System must maintain unique requirement IDs."""
        req = svc.register_requirement(
            requirement_id="TF-DEMO-001",
            title="Demo Requirement",
            target_version="Foundation",
            priority="P0",
            location="global",
            domain="demo",
            personal_dri="John Doe",
        )
        assert req.requirement_id == "TF-DEMO-001"
        assert req.status == RequirementStatus.DISCOVERED
        assert req.title == "Demo Requirement"
        assert req.priority == "P0"

    def test_register_duplicate_id_raises_conflict(self, svc: GovernanceService) -> None:
        """FR-10: Duplicate ID must be rejected."""
        svc.register_requirement("TF-DUP-001", "First")
        with pytest.raises(ConflictError):
            svc.register_requirement("TF-DUP-001", "Second")

    def test_register_requirement_with_detailed_doc(self, svc: GovernanceService) -> None:
        """FR-3: Detailed document URL gets attached as evidence."""
        req = svc.register_requirement(
            "TF-DOC-001",
            "With Doc",
            detailed_doc_url="docs/requirements/TF-DOC-001.md",
        )
        assert len(req.evidence_links) == 1
        assert req.evidence_links[0].type.value == "detailed_requirement"
        assert "TF-DOC-001.md" in req.evidence_links[0].url

    def test_empty_requirement_id_rejected(self, svc: GovernanceService) -> None:
        """FR-1: Empty ID must be rejected."""
        with pytest.raises(ValidationError_):
            svc.register_requirement("", "Empty ID")

    def test_get_nonexistent_requirement(self, svc: GovernanceService) -> None:
        """AC-1: Retrieving nonexistent requirement raises 404."""
        with pytest.raises(NotFoundError):
            svc.get_requirement("TF-NONEXIST-001")


class TestRequirementTransitions:
    """State machine transitions."""

    def test_discovered_to_defined(self, svc: GovernanceService) -> None:
        """Valid: discovered -> defined."""
        svc.register_requirement("TF-TRANS-001", "Transition Test")
        updated = svc.transition_requirement("TF-TRANS-001", RequirementStatus.DEFINED)
        assert updated.status == RequirementStatus.DEFINED

    def test_defined_to_reviewed(self, svc: GovernanceService) -> None:
        """Valid: defined -> reviewed."""
        svc.register_requirement("TF-TRANS-002", "Review Test")
        svc.transition_requirement("TF-TRANS-002", RequirementStatus.DEFINED)
        updated = svc.transition_requirement("TF-TRANS-002", RequirementStatus.REVIEWED)
        assert updated.status == RequirementStatus.REVIEWED

    def test_reviewed_to_approved_requires_detailed_doc(self, svc: GovernanceService) -> None:
        """FR-3: Cannot approve without detailed doc."""
        svc.register_requirement("TF-APPROVE-001", "No Doc")
        svc.transition_requirement("TF-APPROVE-001", RequirementStatus.DEFINED)
        svc.transition_requirement("TF-APPROVE-001", RequirementStatus.REVIEWED)
        with pytest.raises(ValidationError_, match="detailed document"):
            svc.transition_requirement("TF-APPROVE-001", RequirementStatus.APPROVED)

    def test_approved_with_detailed_doc_succeeds(self, svc: GovernanceService) -> None:
        """FR-3: With detailed doc, approval succeeds."""
        svc.register_requirement(
            "TF-APPROVE-002",
            "With Doc",
            detailed_doc_url="docs/requirements/TF-APPROVE-002.md",
        )
        svc.transition_requirement("TF-APPROVE-002", RequirementStatus.DEFINED)
        svc.transition_requirement("TF-APPROVE-002", RequirementStatus.REVIEWED)
        updated = svc.transition_requirement("TF-APPROVE-002", RequirementStatus.APPROVED)
        assert updated.status == RequirementStatus.APPROVED

    def test_invalid_transition_rejected(self, svc: GovernanceService) -> None:
        """State machine enforces valid transitions."""
        svc.register_requirement("TF-INVALID-001", "Invalid Transition")
        # discovered -> released is not allowed
        with pytest.raises(ValidationError_):
            svc.transition_requirement("TF-INVALID-001", RequirementStatus.RELEASED)

    def test_full_lifecycle(self, svc: GovernanceService) -> None:
        """Happy path: discovered -> defined -> reviewed -> approved -> in_delivery -> implemented -> verified -> released."""
        svc.register_requirement(
            "TF-LIFECYCLE-001",
            "Full Cycle",
            detailed_doc_url="docs/requirements/TF-LIFECYCLE-001.md",
            dependencies=[],
        )
        expected_states = [
            RequirementStatus.DISCOVERED,
            RequirementStatus.DEFINED,
            RequirementStatus.REVIEWED,
            RequirementStatus.APPROVED,
            RequirementStatus.IN_DELIVERY,
            RequirementStatus.IMPLEMENTED,
            RequirementStatus.VERIFIED,
            RequirementStatus.RELEASED,
        ]
        for i, state in enumerate(expected_states[1:], start=1):
            updated = svc.transition_requirement("TF-LIFECYCLE-001", state)
            assert updated.status == state, f"Failed at step {i}: {state}"

    def test_deferred_transitions_back(self, svc: GovernanceService) -> None:
        """Deferred requirements can be re-activated."""
        svc.register_requirement("TF-DEFER-001", "Deferred Test")
        svc.transition_requirement("TF-DEFER-001", RequirementStatus.DEFERRED)
        assert svc.get_requirement("TF-DEFER-001").status == RequirementStatus.DEFERRED
        svc.transition_requirement("TF-DEFER-001", RequirementStatus.DISCOVERED)
        assert svc.get_requirement("TF-DEFER-001").status == RequirementStatus.DISCOVERED


class TestRequirementIntegrity:
    """FR-10: Governance checks."""

    def test_unknown_dependency_detected(self, svc: GovernanceService) -> None:
        """FR-10: Unknown dependencies detected by integrity check."""
        svc.register_requirement("TF-INTEG-001", "Integrity Test", dependencies=["UNKNOWN-001"])
        issues = svc.validate_requirement_integrity("TF-INTEG-001")
        assert any("UNKNOWN-001" in issue for issue in issues)

    def test_self_dependency_detected(self, svc: GovernanceService) -> None:
        """FR-10: Self-dependency is flagged."""
        svc.register_requirement("TF-SELF-001", "Self Dep", dependencies=["TF-SELF-001"])
        issues = svc.validate_requirement_integrity("TF-SELF-001")
        assert any("Self-dependency" in issue for issue in issues)


class TestReleaseGate:
    """FR-8, AC-3: Release gate."""

    def test_clean_release_gate(self, svc: GovernanceService) -> None:
        """No blockers — gate passes."""
        result = svc.check_release_gate("Foundation")
        assert result["is_blocked"] is False
        assert len(result["blocked_items"]) == 0

    def test_released_without_detailed_doc_blocked(self, svc: GovernanceService) -> None:
        """AC-3 (FR-3): Cannot approve without detailed document."""
        svc.register_requirement("TF-GATE-001", "Gate Test", target_version="Foundation")
        svc.transition_requirement("TF-GATE-001", RequirementStatus.DEFINED)
        svc.transition_requirement("TF-GATE-001", RequirementStatus.REVIEWED)
        # Trying to approve without detailed doc should fail
        with pytest.raises(ValidationError_) as exc:
            svc.transition_requirement("TF-GATE-001", RequirementStatus.APPROVED)
        assert "detailed document" in str(exc.value).lower() or "FR-3" in str(exc.value)

    def test_release_gate_blocks_unreviewed_components(self, svc: GovernanceService) -> None:
        """CANDIDATE components block the release gate."""
        svc.register_component("unreviewed-lib", "https://example.com/repo", "abc123", "MIT")
        result = svc.check_release_gate("Foundation")
        assert result["is_blocked"] is True
        assert any("unreviewed-lib" in str(c) for c in result["unreviewed_components"])

    def test_release_gate_passes_with_completed_requirements(self, svc: GovernanceService) -> None:
        """Release gate passes when all requirements are properly documented and released."""
        svc.register_requirement("TF-REQ-001", "Test Req", target_version="Foundation", detailed_doc_url="docs/requirements/TF-TEST.md")
        svc.transition_requirement("TF-REQ-001", RequirementStatus.DEFINED)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.REVIEWED)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.APPROVED)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.IN_DELIVERY)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.IMPLEMENTED)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.VERIFIED)
        svc.transition_requirement("TF-REQ-001", RequirementStatus.RELEASED)
        result = svc.check_release_gate("Foundation")
        assert result["is_blocked"] is False

# ---------------------------------------------------------------------------
# Change Request Flow — TF-GOV-001
# ---------------------------------------------------------------------------


class TestChangeRequestFlow:
    """FR-4, FR-5, FR-6."""

    def test_create_change_request(self, svc: GovernanceService) -> None:
        """FR-4: Change request records reason and scope."""
        cr = svc.create_change_request(
            proposer="Alice",
            reason="Scope expansion needed for Foundation",
            affected_ids=["TF-GOV-001", "TF-GOV-002"],
        )
        assert cr.proposer == "Alice"
        assert "TF-GOV-001" in cr.affected_ids
        assert cr.decision == ""

    def test_approve_change_request(self, svc: GovernanceService) -> None:
        """Change request can be approved."""
        cr = svc.create_change_request(
            proposer="Bob",
            reason="Update version target",
            affected_ids=["TF-DEMO-001"],
        )
        updated = svc.approve_change_request(str(cr.change_id), "approved", "Charlie")
        assert updated.decision == "approved"
        assert updated.decision_by == "Charlie"

    def test_reject_change_request(self, svc: GovernanceService) -> None:
        """Change request can be rejected."""
        cr = svc.create_change_request(proposer="Dave", reason="Not needed")
        updated = svc.approve_change_request(str(cr.change_id), "rejected", "Eve")
        assert updated.decision == "rejected"

    def test_double_decision_rejected(self, svc: GovernanceService) -> None:
        """FR-4: Second decision on same CR rejected."""
        cr = svc.create_change_request(proposer="Frank", reason="Test")
        svc.approve_change_request(str(cr.change_id), "approved", "Grace")
        with pytest.raises(ConflictError):
            svc.approve_change_request(str(cr.change_id), "rejected", "Heidi")


# ---------------------------------------------------------------------------
# Component Lifecycle — TF-GOV-002
# ---------------------------------------------------------------------------


class TestComponentLifecycle:
    """TF-GOV-002 FR-1 to FR-10."""

    def test_register_component(self, svc: GovernanceService) -> None:
        """FR-1: Component registered with SHA and license hash."""
        comp = svc.register_component(
            name="test-lib",
            repository_url="https://github.com/test/test-lib",
            commit_sha="a1b2c3d4e5f6a7b8c9d0",
            license_text="MIT License\n\nCopyright (c) 2026 Test",
        )
        assert comp.name == "test-lib"
        assert comp.commit_sha == "a1b2c3d4e5f6a7b8c9d0"
        assert comp.decision == GovernanceDecision.CANDIDATE
        assert len(comp.license_hash) == 64  # SHA-256

    def test_register_empty_name_rejected(self, svc: GovernanceService) -> None:
        """Empty component name rejected."""
        with pytest.raises(ValidationError_):
            svc.register_component("", "https://example.com/repo", "abc", "MIT")

    def test_register_empty_sha_rejected(self, svc: GovernanceService) -> None:
        """Empty SHA rejected."""
        with pytest.raises(ValidationError_):
            svc.register_component("test", "https://example.com/repo", "", "MIT")

    def test_component_lifecycle_to_approved_reuse(self, svc: GovernanceService) -> None:
        """FR-3–FR-5: Component transitions through lifecycle."""
        svc.register_component("vue-flow", "https://github.com/bcakmakoglu/vue-flow", "d4e5f6a7", "MIT")
        svc.update_component_decision("vue-flow", GovernanceDecision.UNDER_REVIEW)
        svc.update_component_decision(
            "vue-flow",
            GovernanceDecision.APPROVED_REUSE,
            new_evidence="MIT license allows reuse, npm dependency",
            new_notify_obligations="Include MIT notice in distribution",
        )
        comp = svc.get_component("vue-flow")
        assert comp.decision == GovernanceDecision.APPROVED_REUSE
        assert "MIT" in comp.decision_evidence

    def test_component_lifecycle_to_clean_room(self, svc: GovernanceService) -> None:
        """FR-6: Component can be marked clean_room_rewrite."""
        svc.register_component("toonflow-app", "https://github.com/toonflow/app", "a1b2c3d4", "Apache-2.0")
        svc.update_component_decision("toonflow-app", GovernanceDecision.UNDER_REVIEW)
        svc.update_component_decision(
            "toonflow-app",
            GovernanceDecision.CLEAN_ROOM_REWRITE,
            new_evidence="Independent implementation, reference only",
        )
        comp = svc.get_component("toonflow-app")
        assert comp.decision == GovernanceDecision.CLEAN_ROOM_REWRITE

    def test_component_abandoned(self, svc: GovernanceService) -> None:
        """FR-7: Component can be abandoned."""
        svc.register_component("old-lib", "https://example.com/old", "deadbeef", "GPL-3.0")
        svc.update_component_decision("old-lib", GovernanceDecision.ABANDONED,
                                      new_evidence="GPL incompatibility, clean-room alternative exists")
        comp = svc.get_component("old-lib")
        assert comp.decision == GovernanceDecision.ABANDONED

    def test_invalid_component_transition(self, svc: GovernanceService) -> None:
        """Invalid transitions are rejected."""
        svc.register_component("test-lib2", "https://example.com/repo2", "abc123", "MIT")
        # candidate -> approved_reuse is invalid
        with pytest.raises(ValidationError_):
            svc.update_component_decision("test-lib2", GovernanceDecision.APPROVED_REUSE)

    def test_get_nonexistent_component(self, svc: GovernanceService) -> None:
        """Getting a nonexistent component raises NotFound."""
        with pytest.raises(NotFoundError):
            svc.get_component("nonexistent-lib")

    def test_list_components_filtered(self, svc: GovernanceService) -> None:
        """List components can be filtered by decision."""
        svc.register_component("lib-a", "https://example.com/a", "sha1", "MIT")
        svc.register_component("lib-b", "https://example.com/b", "sha2", "Apache-2.0")
        # Both are CANDIDATE by default
        candidates = svc.list_components(decision=GovernanceDecision.CANDIDATE)
        assert len(candidates) == 2
        approved = svc.list_components(decision=GovernanceDecision.APPROVED_REUSE)
        assert len(approved) == 0

    def test_notice_generation(self, svc: GovernanceService) -> None:
        """FR-10: NOTICE file generated from approved_reuse components."""
        svc.register_component("vue-flow", "https://github.com/bcakmakoglu/vue-flow", "d4e5f6a7", "MIT")
        svc.update_component_decision("vue-flow", GovernanceDecision.UNDER_REVIEW)
        svc.update_component_decision("vue-flow", GovernanceDecision.APPROVED_REUSE,
                                      new_notify_obligations="Include MIT notice")
        notice = svc.generate_notice_text()
        assert "vue-flow" in notice
        assert "MIT" in notice
        assert "Third-Party Notices" in notice

    def test_notice_empty_without_approved(self, svc: GovernanceService) -> None:
        """FR-10: Notice is minimal when no approved_reuse components exist."""
        notice = svc.generate_notice_text()
        assert notice is not None

    def test_component_update_obligations(self, svc: GovernanceService) -> None:
        """Obligations can be updated during transition."""
        svc.register_component("webav", "https://github.com/hughfenghen/WebAV", "e5f6a7b8", "MIT")
        svc.update_component_decision("webav", GovernanceDecision.UNDER_REVIEW)
        svc.update_component_decision(
            "webav",
            GovernanceDecision.APPROVED_REUSE,
            new_evidence="MIT license, npm dependency",
            new_notify_obligations="Include MIT notice in distribution package",
        )
        comp = svc.get_component("webav")
        assert "MIT notice" in comp.notify_obligations


class TestRequirementsFiltering:
    """FR-8: Query requirements by domain and version."""

    def test_list_requirements_by_domain(self, svc: GovernanceService) -> None:
        """Can filter requirements by domain."""
        svc.register_requirement("TF-FILTER-001", "Gov Req", domain="governance")
        svc.register_requirement("TF-FILTER-002", "Quality Req", domain="quality")
        svc.register_requirement("TF-FILTER-003", "Another Gov", domain="governance")

        gov_reqs = svc.list_requirements(domain="governance")
        assert len(gov_reqs) == 2
        quality_reqs = svc.list_requirements(domain="quality")
        assert len(quality_reqs) == 1

    def test_list_all_requirements(self, svc: GovernanceService) -> None:
        """No filter returns all."""
        svc.register_requirement("TF-ALL-001", "One")
        svc.register_requirement("TF-ALL-002", "Two")
        all_reqs = svc.list_requirements()
        assert len(all_reqs) == 2
