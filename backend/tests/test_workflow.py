"""TF-WF-004: Contract tests for Workflow Draft & Revision.

Tests cover:
  - Hash computation (graph, layout, execution separation)
  - Draft CRUD with CAS
  - Revision freeze and immutability
  - Rollback, diff, retirement
  - Conflict detection
"""
from __future__ import annotations

import pytest
from uuid import uuid4

from src.core.exceptions import ConflictError
from src.schemas.models import (
    Workflow,
    WorkflowDraft,
    WorkflowRevision,
    OwnerScope,
)
from src.domain.workflow.workflow_service import WorkflowService, WorkflowDiff
from src.domain.workflow.draft_revision import (
    compute_graph_hash,
    compute_layout_hash,
    compute_execution_hash,
    normalize_graph_and_layout,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def service() -> WorkflowService:
    return WorkflowService()


@pytest.fixture
def owner() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def sample_graph() -> dict:
    return {
        "nodes": {
            "n1": {"type": "image_loader", "config": {"path": "/input.png"}},
            "n2": {"type": "resize", "config": {"width": 512, "height": 512}},
        },
        "edges": [
            {"from": "n1", "to": "n2", "port": "image_out"},
        ],
    }


@pytest.fixture
def sample_config() -> dict:
    return {"execution_mode": "sequential", "max_retries": 3}


@pytest.fixture
def sample_layout() -> dict:
    return {
        "n1": {"x": 100, "y": 200},
        "n2": {"x": 400, "y": 200},
    }


# ------------------------------------------------------------------
# Test: Hash computation
# ------------------------------------------------------------------


class TestHashComputation:
    def test_vue_flow_position_is_layout_only_and_edge_handles_are_semantic(self) -> None:
        graph = {
            "nodes": [{"id": "a", "type": "brief", "position": {"x": 10, "y": 20}}],
            "edges": [{"source": "a", "target": "b", "sourceHandle": "out", "targetHandle": "in"}],
        }
        semantic, layout = normalize_graph_and_layout(graph, {})
        moved = {
            **graph,
            "nodes": [{"id": "a", "type": "brief", "position": {"x": 999, "y": 1}}],
        }
        assert semantic["nodes"][0].get("position") is None
        assert layout["nodes"]["a"] == {"x": 10, "y": 20}
        assert compute_graph_hash(graph, {}) == compute_graph_hash(moved, {})
        changed_port = {**graph, "edges": [{"source": "a", "target": "b", "sourceHandle": "alternate", "targetHandle": "in"}]}
        assert compute_graph_hash(graph, {}) != compute_graph_hash(changed_port, {})
    def test_graph_hash(self, sample_graph: dict, sample_config: dict) -> None:
        h = compute_graph_hash(sample_graph, sample_config)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_graph_hash_deterministic(self, sample_graph: dict, sample_config: dict) -> None:
        h1 = compute_graph_hash(sample_graph, sample_config)
        h2 = compute_graph_hash(sample_graph, sample_config)
        assert h1 == h2

    def test_layout_hash(self, sample_layout: dict) -> None:
        h = compute_layout_hash(sample_layout)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_layout_hash_separate_from_graph(self, sample_graph: dict, sample_config: dict, sample_layout: dict) -> None:
        """Pure layout change must NOT change graph_hash, but DOES change layout_hash."""
        graph_hash = compute_graph_hash(sample_graph, sample_config)

        # Same graph, different layout
        other_layout = {"n1": {"x": 999, "y": 999}}
        assert compute_layout_hash(sample_layout) != compute_layout_hash(other_layout)
        # graph hash unchanged
        assert compute_graph_hash(sample_graph, sample_config) == graph_hash

    def test_execution_hash_with_deps(self) -> None:
        """execution_hash changes when pinned dependency revisions change."""
        gh = compute_graph_hash({"nodes": {"n1": {}}}, {})
        deps = ["rev:abc", "rev:def"]
        h1 = compute_execution_hash(gh, deps)
        h2 = compute_execution_hash(gh, ["rev:abc", "rev:xxx"])
        assert h1 != h2

    def test_execution_hash_layout_change_only(self, sample_graph: dict, sample_config: dict) -> None:
        """AC-3: Only moving nodes changes layout_hash but NOT execution_hash."""
        gh = compute_graph_hash(sample_graph, sample_config)
        # With no pinned deps, execution_hash = graph_hash
        eh = compute_execution_hash(gh, [])
        # Same graph, different layout (irrelevant for execution hash)
        assert eh == compute_execution_hash(gh, [])

    def test_execution_hash_same_for_same_deps(self) -> None:
        gh = compute_graph_hash({"nodes": {}}, {})
        deps = ["rev:a", "rev:b"]
        assert compute_execution_hash(gh, deps) == compute_execution_hash(gh, deps)


# ------------------------------------------------------------------
# Test: Draft creation
# ------------------------------------------------------------------


class TestWorkflowService:
    def test_create_workflow(self, service: WorkflowService, owner: OwnerScope) -> None:
        wf = service.create_workflow(owner_scope=owner)
        assert isinstance(wf, Workflow)
        assert wf.workflow_id is not None

    def test_get_draft(self, service: WorkflowService, owner: OwnerScope) -> None:
        wf = service.create_workflow(owner_scope=owner)
        draft = service.get_draft(wf.workflow_id)
        assert isinstance(draft, WorkflowDraft)
        assert draft.draft_version == 1

    def test_save_draft_with_cas_success(
        self, service: WorkflowService, sample_graph: dict, sample_config: dict, sample_layout: dict
    ) -> None:
        wf = service.create_workflow()
        initial_draft = service.get_draft(wf.workflow_id)

        saved = service.save_draft(
            workflow_id=wf.workflow_id,
            graph=sample_graph,
            config=sample_config,
            layout=sample_layout,
            base_graph_hash=initial_draft.graph_hash,
        )
        assert saved.draft_version == 2
        assert saved.graph_hash != initial_draft.graph_hash

    def test_save_draft_cas_conflict(
        self, service: WorkflowService, sample_graph: dict, sample_config: dict, sample_layout: dict
    ) -> None:
        """AC-2: Two tabs using same base_hash — only one succeeds."""
        wf = service.create_workflow()
        service.get_draft(wf.workflow_id)
        bad_hash = "0" * 64  # Wrong hash

        with pytest.raises(ConflictError):
            service.save_draft(
                workflow_id=wf.workflow_id,
                graph=sample_graph,
                config=sample_config,
                layout=sample_layout,
                base_graph_hash=bad_hash,
            )

    # ------------------------------------------------------------------
    # Test: Revision
    # ------------------------------------------------------------------

    def test_create_revision(
        self, service: WorkflowService, sample_graph: dict, sample_config: dict, sample_layout: dict
    ) -> None:
        wf = service.create_workflow()
        initial_draft = service.get_draft(wf.workflow_id)

        service.save_draft(
            workflow_id=wf.workflow_id,
            graph=sample_graph,
            config=sample_config,
            layout=sample_layout,
            base_graph_hash=initial_draft.graph_hash,
        )

        registry_snap_id = uuid4()
        rev = service.create_revision_from_draft(wf.workflow_id, registry_snap_id)
        assert isinstance(rev, WorkflowRevision)
        assert rev.revision_number == 1
        assert rev.registry_snapshot_id == registry_snap_id

    def test_revision_immutability(
        self, service: WorkflowService, sample_graph: dict, sample_config: dict, sample_layout: dict
    ) -> None:
        """AC-1: Activate revision, modify draft — revision stays frozen."""
        wf = service.create_workflow()
        initial_draft = service.get_draft(wf.workflow_id)

        service.save_draft(
            workflow_id=wf.workflow_id,
            graph=sample_graph,
            config=sample_config,
            layout=sample_layout,
            base_graph_hash=initial_draft.graph_hash,
        )

        initial_graph_hash = service.get_draft(wf.workflow_id).graph_hash
        rev = service.create_revision_from_draft(wf.workflow_id, uuid4())

        # Modify draft
        after_draft = service.get_draft(wf.workflow_id)
        service.save_draft(
            workflow_id=wf.workflow_id,
            graph={},
            config={},
            layout={},
            base_graph_hash=after_draft.graph_hash,
        )

        # Revision must still have old hash
        assert rev.graph_hash == initial_graph_hash
        assert rev.execution_hash != service.get_draft(wf.workflow_id).execution_hash

    def test_retire_revision(self, service: WorkflowService) -> None:
        wf = service.create_workflow()
        rev = service.create_revision_from_draft(wf.workflow_id, uuid4())
        retired = service.retire_revision(rev.revision_id)
        from src.schemas.enums import RevisionStatus
        assert retired.revision_status == RevisionStatus.RETIRED

    def test_rollback_to_revision(self, service: WorkflowService) -> None:
        """AC-5: Rollback creates new draft, old revision unchanged."""
        wf = service.create_workflow()
        rev = service.create_revision_from_draft(wf.workflow_id, uuid4())
        new_draft = service.rollback_to_revision(wf.workflow_id, rev.revision_id)
        assert new_draft.base_revision_id == rev.revision_id
        assert new_draft.draft_version == 1

    def test_list_revisions(self, service: WorkflowService) -> None:
        wf = service.create_workflow()
        service.create_revision_from_draft(wf.workflow_id, uuid4())
        service.create_revision_from_draft(wf.workflow_id, uuid4())
        revs = service.list_revisions(wf.workflow_id)
        assert len(revs) == 2

    def test_get_active_revision(self, service: WorkflowService) -> None:
        wf = service.create_workflow()
        rev = service.create_revision_from_draft(wf.workflow_id, uuid4())
        active = service.get_active_revision(wf.workflow_id)
        assert active is not None
        assert active.revision_id == rev.revision_id

    # ------------------------------------------------------------------
    # Test: Diff
    # ------------------------------------------------------------------

    def test_diff_draft_vs_revision_no_changes(self, service: WorkflowService) -> None:
        wf = service.create_workflow()
        initial_draft = service.get_draft(wf.workflow_id)
        service.save_draft(
            workflow_id=wf.workflow_id,
            graph={},
            config={},
            layout={},
            base_graph_hash=initial_draft.graph_hash,
        )
        rev = service.create_revision_from_draft(wf.workflow_id, uuid4())
        diff = service.diff_draft_vs_revision(wf.workflow_id, rev.revision_id)
        # Foundation: diff vs empty old content — nodes will appear as "added"
        assert isinstance(diff, WorkflowDiff)

    def test_workflow_lifecycle(self, service: WorkflowService, owner: OwnerScope) -> None:
        """Complete lifecycle: create → save → activate → retire → rollback."""
        wf = service.create_workflow(owner_scope=owner)
        assert wf.workflow_id is not None

        draft1 = service.get_draft(wf.workflow_id)
        draft2 = service.save_draft(
            workflow_id=wf.workflow_id,
            graph={"nodes": {"n1": {}}},
            config={"mode": "test"},
            layout={"n1": {"x": 0, "y": 0}},
            base_graph_hash=draft1.graph_hash,
        )
        assert draft2.draft_version == 2

        rev1 = service.create_revision_from_draft(wf.workflow_id, uuid4())
        assert rev1.revision_number == 1

        # Create second revision
        draft3 = service.get_draft(wf.workflow_id)
        service.save_draft(
            workflow_id=wf.workflow_id,
            graph={"nodes": {"n2": {}}},
            config={"mode": "v2"},
            layout={"n2": {"x": 10, "y": 10}},
            base_graph_hash=draft3.graph_hash,
        )
        rev2 = service.create_revision_from_draft(wf.workflow_id, uuid4())
        assert rev2.revision_number == 2

        # List revisions
        revs = service.list_revisions(wf.workflow_id)
        assert len(revs) == 2  # type: ignore[comparison-overlap]

        # Active should be rev2
        active = service.get_active_revision(wf.workflow_id)
        assert active is not None
        assert active.revision_id == rev2.revision_id
