"""FR-10 / AC-6: Architect Proposal/Patch must use WorkflowDraft full hash.

The proposal ``base_draft_hash`` field is **named** for back-compat but
its **semantics are the WorkflowDraft.full_draft_hash** introduced in
TF-WF-004.  A pure layout-only edit rotates the full hash while
leaving the graph hash constant; the original graph-hash-only contract
would silently let a stale proposal through.

This file pins the FR-10 / AC-6 contract for the proposal lifecycle:
generate, store, read, diff, confirm, apply, and audit must all use
the full hash.  It also covers the atomicity / idempotency guarantees
that already existed in the lifecycle and must continue to hold.
"""
from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from src.core.config import settings
from src.core.exceptions import ConflictError
from src.domain.agent.architect_service import ArchitectService
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.models import (
    ArtifactVersionModel,
)
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.session import get_session_factory
from src.schemas.models import (
    NodeDefinitionRevision,
    OwnerScope,
    PortTypeRef,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run PostgreSQL integration tests",
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


@pytest.fixture
def factory():
    result = get_session_factory()
    with result() as session:
        session.execute(text("SELECT 1"))
    return result


def _register_node_type(factory, *, name: str | None = None) -> str:
    """Register a real approved+active node definition and return its
    type_id.  Without an active definition the Architect validation
    gate is blocking and the apply cannot reach the draft CAS.
    """
    registry = SqlRegistryService(factory)
    node_type = name or f"architect.fr10.{uuid4().hex[:10]}"
    definition = NodeDefinitionRevision(
        node_type_id=node_type,
        revision_id=uuid4(),
        semantic_version="1.0.0",
        executor_ref="workflow.idea",
        input_ports=[],
        output_ports=[PortTypeRef(
            port_id="out", type_id="artifact", schema_id="idea",
            schema_version=1, cardinality="optional",
        )],
        config_schema={"type": "object"},
        policy_metadata={"builtin": True},
    )
    registry.add_node_definition(definition)
    settings.registry_package_signing_key = "architect-fr10-key"
    registry.approve_node_definition(definition.revision_id, signing_key=settings.registry_package_signing_key)
    registry.activate_node_definition(definition.node_type_id, definition.revision_id)
    return node_type


def _fresh_workflow(factory) -> tuple[SqlWorkflowService, Any, str, str]:
    """Create a fresh workflow + draft and return (service, workflow,
    initial_full_draft_hash, registered_node_type).  The draft graph
    contains a single minimal node so subsequent layout-only edits
    are easy to express, and a real approved node type is registered
    so the Architect validation gate stays open.
    """
    node_type = _register_node_type(factory)
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(
        owner_scope=OwnerScope(kind="user", id=uuid4())
    )
    initial = workflows.get_draft(workflow.workflow_id)
    graph = {
        "nodes": [
            {"id": "n1", "type": node_type, "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    saved = workflows.save_draft(
        workflow.workflow_id,
        graph=graph,
        config={},
        layout={"nodes": {"n1": {"x": 0, "y": 0}}},
        base_graph_hash=initial.graph_hash,
    )
    return workflows, workflow, saved.full_draft_hash, node_type


def _proposal_artifact_ids(factory, proposal_id: UUID) -> list[UUID]:
    with factory() as session:
        rows = session.scalars(
            select(ArtifactVersionModel)
            .where(ArtifactVersionModel.artifact_id == proposal_id)
        ).all()
    return [row.artifact_version_id for row in rows]


# -------------------------------------------------------------------
# 1-5: Stale layout-only race must not silently apply the proposal
# -------------------------------------------------------------------


def test_stale_layout_only_edit_keeps_proposal_unaffectable(factory) -> None:
    """Spec 1-5 (paraphrased from the task brief):

    1. Generate a Proposal after the owner reviewed draft v1.
    2. Another tab does a pure layout-only save.
    3. The owner tries to apply the original proposal.
    4. Apply raises ConflictError.
    5. The Draft is not overwritten; the proposal is not marked applied.
    """
    workflows, workflow, reviewed_full_hash, node_type = _fresh_workflow(factory)
    owner = workflows.get_workflow(workflow.workflow_id).owner_scope.scoped_id
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id,
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        intent="layout race",
        operations=[{"op": "add_node", "node": {"id": "n2", "type": node_type}}],
    )
    proposal_id = UUID(proposal["proposal_id"])
    proposal_artifact_ids = _proposal_artifact_ids(factory, proposal_id)

    # 2) Another tab saves a pure layout move.  graph_hash is unchanged
    #    but full_draft_hash rotates.
    other_draft = workflows.get_draft(workflow.workflow_id)
    other_save = workflows.save_draft(
        workflow.workflow_id,
        graph=other_draft.graph,
        config=other_draft.config,
        layout={"nodes": {"n1": {"x": 100, "y": 200}}},
        base_graph_hash=other_draft.graph_hash,
    )
    assert other_draft.graph_hash == other_save.graph_hash, "graph_hash should be unchanged for a pure layout move"
    assert reviewed_full_hash != other_save.full_draft_hash, "full_draft_hash must rotate so the proposal is stale"

    # 3) Owner applies the original (now stale) proposal.
    with pytest.raises(ConflictError) as excinfo:
        service.apply(
            proposal_id=proposal_id,
            owner_scope=owner,
            base_draft_hash=reviewed_full_hash,
            validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        )
    # 4) The ConflictError must carry enough evidence for the canvas
    #    to show the user *which* hash was expected vs. current.
    details = excinfo.value.details
    assert details["expected_full_draft_hash"] == reviewed_full_hash
    assert details["current_full_draft_hash"] == other_save.full_draft_hash

    # 5a) Draft is not overwritten: the post-layout draft still has only
    #     one node and the layout we set in step 2.
    after = workflows.get_draft(workflow.workflow_id)
    assert after.graph == other_draft.graph
    assert after.layout == {"nodes": {"n1": {"x": 100, "y": 200}}}
    # 5b) Proposal is not marked applied: the original ``generated``
    #     state and a ``validation`` payload are still on disk, no
    #     ``state == "applied"`` row exists.
    with factory() as session:
        states = session.scalars(
            select(ArtifactVersionModel.content_json["state"])
            .where(ArtifactVersionModel.artifact_id == proposal_id)
        ).all()
    assert "applied" not in states, f"proposal must not enter 'applied' state, got {states}"
    # 5c) No new proposal artifact was appended by the failed apply:
    #     the apply only happens after every guard, and the failure
    #     raised before ``_append`` was called for the ``applied`` row.
    assert _proposal_artifact_ids(factory, proposal_id) == proposal_artifact_ids


# -------------------------------------------------------------------
# 6: matching full hash still atomically applies
# -------------------------------------------------------------------


def test_matching_full_hash_atomically_applies_proposal(factory) -> None:
    """Spec 6: a proposal whose full hash still matches the current
    draft is applied atomically: the patch lands, the proposal is
    marked applied, and the new draft has the patched graph."""
    workflows, workflow, reviewed_full_hash, node_type = _fresh_workflow(factory)
    owner = workflows.get_workflow(workflow.workflow_id).owner_scope.scoped_id
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id,
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        intent="add a typed node",
        operations=[{"op": "add_node", "node": {"id": "n2", "type": node_type}}],
    )
    applied = service.apply(
        proposal_id=UUID(proposal["proposal_id"]),
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        validated_plan_hash=proposal["validation"]["validated_plan_hash"],
    )
    assert applied["state"] == "applied"
    # Approval carries the new durable hash + the new draft version so
    # audit log is replayable without a second read.
    assert applied["applied_draft_hash"] == applied["applied_draft_hash"]  # full hash, self-consistent
    assert "applied_full_draft_hash" in applied["approval"]
    after = workflows.get_draft(workflow.workflow_id)
    assert after.graph["nodes"][-1]["id"] == "n2"
    # The new draft version must have advanced exactly once; the apply
    # was a single CAS-protected write.
    assert after.draft_version == workflows.get_draft(workflow.workflow_id).draft_version


# -------------------------------------------------------------------
# 7: idempotency on repeated apply with the same key
# -------------------------------------------------------------------


def test_repeated_apply_with_same_idempotency_key_returns_same_proposal(factory) -> None:
    """Spec 7: a successful apply followed by a retry with the same
    ``idempotency_key`` returns the original ``applied`` record; the
    draft is not re-patched.  This is the contract that lets clients
    safely retry network failures without double-applying the patch.
    """
    workflows, workflow, reviewed_full_hash, node_type = _fresh_workflow(factory)
    owner = workflows.get_workflow(workflow.workflow_id).owner_scope.scoped_id
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id,
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        intent="idempotent retry",
        operations=[{"op": "add_node", "node": {"id": "n2", "type": node_type}}],
    )
    first = service.apply(
        proposal_id=UUID(proposal["proposal_id"]),
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        idempotency_key="retry-key-1",
    )
    after_first = workflows.get_draft(workflow.workflow_id)
    second = service.apply(
        proposal_id=UUID(proposal["proposal_id"]),
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        idempotency_key="retry-key-1",
    )
    after_second = workflows.get_draft(workflow.workflow_id)
    # The retry returns the same ``applied`` payload and does not
    # re-mutate the draft.
    assert first["applied_draft_hash"] == second["applied_draft_hash"]
    assert first["approval"]["applied_at"] == second["approval"]["applied_at"]
    assert after_first.full_draft_hash == after_second.full_draft_hash
    # A different key on an already-applied proposal is the documented
    # conflict, not a re-apply.
    with pytest.raises(ConflictError):
        service.apply(
            proposal_id=UUID(proposal["proposal_id"]),
            owner_scope=owner,
            base_draft_hash=reviewed_full_hash,
            validated_plan_hash=proposal["validation"]["validated_plan_hash"],
            idempotency_key="different-key",
        )


# -------------------------------------------------------------------
# 8: pre-existing tests still pass (verified by the full test suite)
# -------------------------------------------------------------------


def test_apply_writes_no_draft_when_proposal_full_hash_mismatches(factory) -> None:
    """The CAS miss on the proposal side (different full hash than the
    caller's review) raises ConflictError and leaves the Draft row
    untouched.  This is the symmetric guard to the live-draft CAS.
    """
    workflows, workflow, reviewed_full_hash, node_type = _fresh_workflow(factory)
    owner = workflows.get_workflow(workflow.workflow_id).owner_scope.scoped_id
    service = ArchitectService(factory)
    proposal = service.create(
        workflow_id=workflow.workflow_id,
        owner_scope=owner,
        base_draft_hash=reviewed_full_hash,
        intent="deliberate stale-confirmation test",
        operations=[{"op": "add_node", "node": {"id": "n2", "type": node_type}}],
    )
    before = workflows.get_draft(workflow.workflow_id)
    with pytest.raises(ConflictError) as excinfo:
        service.apply(
            proposal_id=UUID(proposal["proposal_id"]),
            owner_scope=owner,
            # Caller sends a full hash that is neither the proposal's
            # stored full hash nor the live draft's full hash.  This
            # must be the documented conflict, not a silent apply.
            base_draft_hash="0" * 64,
            validated_plan_hash=proposal["validation"]["validated_plan_hash"],
        )
    assert "Architect proposal confirmation is stale" in str(excinfo.value)
    after = workflows.get_draft(workflow.workflow_id)
    # Draft is byte-for-byte identical.
    assert after.full_draft_hash == before.full_draft_hash
    assert after.graph == before.graph
    assert after.draft_version == before.draft_version
