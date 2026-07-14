"""TF-WF-004: Workflow Service

Manages Workflow CRUD, Draft save/load with base_hash CAS,
Revision creation, and diff generation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.exceptions import ConflictError, NotFoundError
from src.schemas.models import OwnerScope, Workflow, WorkflowDraft, WorkflowRevision
from src.schemas.enums import RevisionStatus

from .draft_revision import (
    compute_draft_hashes,
    compute_diff,
    create_draft,
    create_revision,
    WorkflowDiff,
)


class WorkflowService:
    """In-memory workflow store for Foundation stage."""

    def __init__(self) -> None:
        # workflow_id -> Workflow
        self._workflows: dict[UUID, Workflow] = {}
        # workflow_id -> WorkflowDraft | None
        self._drafts: dict[UUID, WorkflowDraft] = {}
        # revision_id -> WorkflowRevision
        self._revisions: dict[UUID, WorkflowRevision] = {}
        # workflow_id -> list[revision_id] (ordered by revision_number)
        self._workflow_revisions: dict[UUID, list[WorkflowRevision]] = {}
        # workflow_id -> active revision id
        self._active_revision: dict[UUID, UUID | None] = {}

    # ------------------------------------------------------------------
    # Workflow CRUD
    # ------------------------------------------------------------------

    def create_workflow(self, workflow_id: UUID | None = None, owner_scope: OwnerScope | None = None) -> Workflow:
        """Create a new Workflow with an empty draft."""
        wf_id = workflow_id or uuid4()
        wf = Workflow(
            workflow_id=wf_id,
            owner_scope=owner_scope or OwnerScope(kind="user", id=uuid4()),
            created_at=datetime.now(timezone.utc),
        )
        self._workflows[wf_id] = wf
        # Create initial empty draft
        draft = create_draft(workflow_id=wf_id, draft_version=1)
        self._drafts[wf_id] = draft
        self._workflow_revisions[wf_id] = []
        self._active_revision[wf_id] = None
        return wf

    def get_workflow(self, workflow_id: UUID) -> Workflow:
        wf = self._workflows.get(workflow_id)
        if wf is None:
            raise NotFoundError("Workflow", str(workflow_id))
        return wf

    def list_workflows(
        self, owner_scope: Any = None, offset: int = 0, limit: int = 50
    ) -> list[Workflow]:
        results = list(self._workflows.values())
        if owner_scope is not None:
            results = [w for w in results if w.owner_scope == owner_scope]
        return results[offset : offset + limit]

    def delete_workflow(self, workflow_id: UUID) -> None:
        if workflow_id not in self._workflows:
            raise NotFoundError("Workflow", str(workflow_id))
        del self._workflows[workflow_id]
        self._drafts.pop(workflow_id, None)
        revs = self._workflow_revisions.pop(workflow_id, [])
        for r in revs:
            self._revisions.pop(r.revision_id, None)
        self._active_revision.pop(workflow_id, None)

    # ------------------------------------------------------------------
    # Draft operations
    # ------------------------------------------------------------------

    def get_draft(self, workflow_id: UUID) -> WorkflowDraft:
        self.get_workflow(workflow_id)
        draft = self._drafts.get(workflow_id)
        if draft is None:
            raise NotFoundError("WorkflowDraft", str(workflow_id))
        return draft

    def save_draft(
        self,
        workflow_id: UUID,
        graph: dict[str, Any],
        config: dict[str, Any],
        layout: dict[str, Any],
        base_graph_hash: str,
        pinned_dependency_revisions: list[str] | None = None,
    ) -> WorkflowDraft:
        """Save draft with compare-and-swap on base_graph_hash.

        Raises:
            ConflictError: If the current draft's graph_hash differs from
                          base_graph_hash (another edit happened first).
        """
        current_draft = self.get_draft(workflow_id)

        # CAS check
        if current_draft.graph_hash != base_graph_hash:
            raise ConflictError(
                message=(
                    f"WorkflowDraft {workflow_id} 冲突: "
                    f"base_hash {base_graph_hash} 不匹配当前 {current_draft.graph_hash}"
                )
            )

        # Compute new hashes
        graph_hash, layout_hash, execution_hash = compute_draft_hashes(
            graph, config, layout, pinned_dependency_revisions
        )

        new_draft = WorkflowDraft(
            workflow_id=workflow_id,
            draft_version=current_draft.draft_version + 1,
            base_revision_id=current_draft.base_revision_id,
            graph=graph,
            config=config,
            layout=layout,
            graph_hash=graph_hash,
            layout_hash=layout_hash,
            execution_hash=execution_hash,
            updated_at=datetime.now(timezone.utc),
        )
        self._drafts[workflow_id] = new_draft
        return new_draft

    # ------------------------------------------------------------------
    # Revision operations
    # ------------------------------------------------------------------

    def create_revision_from_draft(
        self,
        workflow_id: UUID,
        registry_snapshot_id: UUID,
    ) -> WorkflowRevision:
        """Freeze the current draft into an immutable WorkflowRevision.

        Raises:
            NotFoundError: If workflow or draft not found.
        """
        draft = self.get_draft(workflow_id)
        revisions = self._workflow_revisions.setdefault(workflow_id, [])
        rev_number = (revisions[-1].revision_number + 1) if revisions else 1

        revision = create_revision(
            workflow_id=workflow_id,
            draft=draft,
            registry_snapshot_id=registry_snapshot_id,
            revision_number=rev_number,
        )
        self._revisions[revision.revision_id] = revision
        revisions.append(revision)
        self._active_revision[workflow_id] = revision.revision_id
        return revision

    def get_revision(self, revision_id: UUID) -> WorkflowRevision:
        rev = self._revisions.get(revision_id)
        if rev is None:
            raise NotFoundError("WorkflowRevision", str(revision_id))
        return rev

    def get_revision_graph(self, revision_id: UUID) -> dict[str, Any]:
        """Return the immutable graph frozen with a workflow revision."""
        return self.get_revision(revision_id).graph

    def list_revisions(
        self, workflow_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[WorkflowRevision]:
        """List all revisions for a workflow, newest first."""
        revisions = self._workflow_revisions.get(workflow_id, [])
        return sorted(revisions, key=lambda r: r.revision_number, reverse=True)[
            offset : offset + limit
        ]

    def get_active_revision(self, workflow_id: UUID) -> WorkflowRevision | None:
        active_id = self._active_revision.get(workflow_id)
        if active_id is None:
            return None
        return self._revisions.get(active_id)

    def retire_revision(self, revision_id: UUID) -> WorkflowRevision:
        """Mark a revision as retired (not used for new runs)."""
        rev = self.get_revision(revision_id)
        rev.revision_status = RevisionStatus.RETIRED
        # If it was the active revision, clear active pointer
        wf_id = rev.workflow_id
        if self._active_revision.get(wf_id) == revision_id:
            self._active_revision[wf_id] = None
        return rev

    def rollback_to_revision(
        self, workflow_id: UUID, revision_id: UUID
    ) -> WorkflowDraft:
        """Create a new Draft from an old Revision's content.

        Note: Since we store full content in Drafts only, this creates a
        new empty Draft. In V0 with persistent storage, we'll reconstruct
        the graph/layout from the stored Revision content.
        """
        rev = self.get_revision(revision_id)
        if rev.workflow_id != workflow_id:
            raise NotFoundError("WorkflowRevision", str(revision_id))

        # Create new draft pointing to the revision as base
        draft = create_draft(
            workflow_id=workflow_id,
            draft_version=1,
            base_revision_id=revision_id,
        )
        self._drafts[workflow_id] = draft
        return draft

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff_draft_vs_revision(
        self,
        workflow_id: UUID,
        revision_id: UUID | None = None,
    ) -> WorkflowDiff:
        """Diff current draft against a revision (or active revision if None)."""
        draft = self.get_draft(workflow_id)

        if revision_id is None:
            active_rev = self.get_active_revision(workflow_id)
            if active_rev is None:
                # No active revision — everything is new
                return WorkflowDiff(
                    nodes_added=list(draft.graph.get("nodes", {}).keys()),
                    nodes_removed=[],
                    nodes_modified=[],
                    edges_added=[],
                    edges_removed=[],
                    config_changed=True if draft.config else False,
                    layout_changed=True if draft.layout else False,
                    pinned_deps_changed=False,
                )
            revision_id = active_rev.revision_id

        rev = self.get_revision(revision_id)
        if rev.workflow_id != workflow_id:
            raise NotFoundError("WorkflowRevision", str(revision_id))

        # Foundation: diff against empty if no old draft content stored
        old_graph: dict[str, Any] = {}
        old_config: dict[str, Any] = {}
        old_layout: dict[str, Any] = {}

        return compute_diff(
            old_graph=old_graph,
            new_graph=draft.graph,
            old_config=old_config,
            new_config=draft.config,
            old_layout=old_layout,
            new_layout=draft.layout,
        )
