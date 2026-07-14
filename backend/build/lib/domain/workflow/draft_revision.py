"""TF-WF-004: WorkflowDraft and WorkflowRevision hash computation.

Hash rules (Foundation contract):
  - graph_hash = sha256(nodes + edges + config) — semantic content only
  - layout_hash = sha256(positions + visual metadata) — visual-only
  - execution_hash = sha256(graph_hash + all pinned dependency revisions)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.schemas.enums import RevisionStatus
from src.schemas.models import WorkflowDraft, WorkflowRevision


def compute_graph_hash(
    graph: dict[str, Any], config: dict[str, Any]
) -> str:
    """Deterministic hash of graph (nodes + edges) + config.

    Uses sorted JSON keys for reproducibility.
    """
    hasher = hashlib.sha256()
    hasher.update(json.dumps(graph, sort_keys=True, default=str).encode())
    hasher.update(b"|")
    hasher.update(json.dumps(config, sort_keys=True, default=str).encode())
    return hasher.hexdigest()


def compute_layout_hash(layout: dict[str, Any]) -> str:
    """Deterministic hash of layout (positions, zoom, visual metadata)."""
    hasher = hashlib.sha256()
    hasher.update(json.dumps(layout, sort_keys=True, default=str).encode())
    return hasher.hexdigest()


def compute_execution_hash(
    graph_hash: str,
    pinned_dependency_revisions: list[str],
) -> str:
    """execution_hash = graph_hash + all pinned dependency revision hashes.

    Pinned dependency revisions include fixed ResourceRef/ArtifactRef revision IDs,
    node definition revision IDs, and converter revision IDs.
    """
    hasher = hashlib.sha256()
    hasher.update(graph_hash.encode())
    for dep in sorted(pinned_dependency_revisions):
        hasher.update(b"|")
        hasher.update(dep.encode())
    return hasher.hexdigest()


def compute_draft_hashes(
    graph: dict[str, Any],
    config: dict[str, Any],
    layout: dict[str, Any],
    pinned_dependency_revisions: list[str] | None = None,
) -> tuple[str, str, str]:
    """Compute all three hashes for a draft in one call.

    Returns (graph_hash, layout_hash, execution_hash).
    """
    graph_hash = compute_graph_hash(graph, config)
    layout_hash = compute_layout_hash(layout)
    execution_hash = compute_execution_hash(
        graph_hash, pinned_dependency_revisions or []
    )
    return graph_hash, layout_hash, execution_hash


def create_draft(
    workflow_id: UUID,
    draft_version: int = 1,
    base_revision_id: UUID | None = None,
    graph: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    layout: dict[str, Any] | None = None,
    pinned_dependency_revisions: list[str] | None = None,
) -> WorkflowDraft:
    """Factory: create a WorkflowDraft with computed hashes."""
    graph = graph or {}
    config = config or {}
    layout = layout or {}

    graph_hash, layout_hash, execution_hash = compute_draft_hashes(
        graph, config, layout, pinned_dependency_revisions
    )

    return WorkflowDraft(
        workflow_id=workflow_id,
        draft_version=draft_version,
        base_revision_id=base_revision_id,
        graph=graph,
        config=config,
        layout=layout,
        graph_hash=graph_hash,
        layout_hash=layout_hash,
        execution_hash=execution_hash,
        updated_at=datetime.now(timezone.utc),
    )


def create_revision(
    workflow_id: UUID,
    draft: WorkflowDraft,
    registry_snapshot_id: UUID,
    revision_number: int = 1,
) -> WorkflowRevision:
    """Factory: freeze a WorkflowDraft into an immutable WorkflowRevision."""
    return WorkflowRevision(
        workflow_id=workflow_id,
        revision_id=uuid4(),
        revision_number=revision_number,
        graph_hash=draft.graph_hash,
        execution_hash=draft.execution_hash,
        registry_snapshot_id=registry_snapshot_id,
        revision_status=RevisionStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
    )


# ------------------------------------------------------------------
# Diff helpers (Foundation: basic structural comparison)
# ------------------------------------------------------------------

class WorkflowDiff:
    """Structural diff between two workflow drafts or revisions."""

    def __init__(
        self,
        nodes_added: list[str],
        nodes_removed: list[str],
        nodes_modified: list[str],
        edges_added: list[str],
        edges_removed: list[str],
        config_changed: bool,
        layout_changed: bool,
        pinned_deps_changed: bool,
    ) -> None:
        self.nodes_added = nodes_added
        self.nodes_removed = nodes_removed
        self.nodes_modified = nodes_modified
        self.edges_added = edges_added
        self.edges_removed = edges_removed
        self.config_changed = config_changed
        self.layout_changed = layout_changed
        self.pinned_deps_changed = pinned_deps_changed

    def has_semantic_changes(self) -> bool:
        """True if anything beyond layout changed."""
        return bool(
            self.nodes_added
            or self.nodes_removed
            or self.nodes_modified
            or self.edges_added
            or self.edges_removed
            or self.config_changed
            or self.pinned_deps_changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes_added": self.nodes_added,
            "nodes_removed": self.nodes_removed,
            "nodes_modified": self.nodes_modified,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "config_changed": self.config_changed,
            "layout_changed": self.layout_changed,
            "pinned_deps_changed": self.pinned_deps_changed,
        }


def compute_diff(
    old_graph: dict[str, Any],
    new_graph: dict[str, Any],
    old_config: dict[str, Any],
    new_config: dict[str, Any],
    old_layout: dict[str, Any],
    new_layout: dict[str, Any],
    old_pinned_deps: list[str] | None = None,
    new_pinned_deps: list[str] | None = None,
) -> WorkflowDiff:
    """Compute structural diff between two workflow versions.

    Foundation version uses simple set-based comparison on node IDs,
    edge IDs, and top-level config keys.
    """
    old_nodes = set(old_graph.get("nodes", {}).keys())
    new_nodes = set(new_graph.get("nodes", {}).keys())

    old_edges = {_edge_key(e) for e in old_graph.get("edges", [])}
    new_edges = {_edge_key(e) for e in new_graph.get("edges", [])}

    # Detect modified nodes (present in both, but different content)
    old_node_data = old_graph.get("nodes", {})
    new_node_data = new_graph.get("nodes", {})
    common_nodes = old_nodes & new_nodes
    modified_nodes = [
        nid
        for nid in common_nodes
        if json.dumps(old_node_data.get(nid, {}), sort_keys=True, default=str)
        != json.dumps(new_node_data.get(nid, {}), sort_keys=True, default=str)
    ]

    return WorkflowDiff(
        nodes_added=sorted(new_nodes - old_nodes),
        nodes_removed=sorted(old_nodes - new_nodes),
        nodes_modified=sorted(modified_nodes),
        edges_added=sorted(new_edges - old_edges),
        edges_removed=sorted(old_edges - new_edges),
        config_changed=old_config != new_config,
        layout_changed=old_layout != new_layout,
        pinned_deps_changed=(old_pinned_deps or []) != (new_pinned_deps or []),
    )


def _edge_key(edge: Any) -> str:
    if isinstance(edge, dict):
        return f"{edge.get('from', '')}->{edge.get('to', '')}--{edge.get('port', '')}"
    return str(edge)
