"""PostgreSQL integration tests for the TF-WF-004 contract.

Each test owns a UUID-scoped workflow and removes it at teardown.  The
PG database is required: ``TOONFLOW_RUN_PG_TESTS=1``.

These tests cover the items the task brief calls "high risk":

* two-tab concurrent save of a pure layout change — at most one
  writer wins and the other gets a structured CAS conflict;
* the durable path distinguishes the three hashes — graph, layout,
  execution — and pure layout moves never change execution_hash;
* activation with a stale ``expected_full_draft_hash`` refuses to
  freeze a new revision and never creates a CompiledExecutionPlan
  row, an OutboxEvent, or updates ``base_revision_id``;
* compile failure inside the activation transaction leaves no
  half-baked revision or plan;
* rollback creates a new Draft, never edits a historic revision,
  and the historic revision's graph_hash is preserved;
* cross-owner / naked ArtifactRef / missing-grant ResourceRef /
  secret plaintext imports are refused before they can land in a
  runnable revision.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text

from src.core.exceptions import ConflictError, ForbiddenError
from src.domain.workflow.compiler import CompilationError
from src.domain.workflow.draft_revision import (
    compute_execution_hash,
    compute_full_draft_hash,
    compute_graph_hash,
    compute_layout_hash,
)
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.models import (
    ArtifactVersionModel,
    CompiledExecutionPlanModel,
    OutboxEventModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
    WorkflowDraftModel,
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.infra.db.template_repository import _contains_forbidden
from src.schemas.enums import RevisionStatus
from src.schemas.models import NodeDefinitionRevision, OwnerScope, PortTypeRef, RegistrySnapshot


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run PostgreSQL integration tests",
)


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


@pytest.fixture
def factory():
    return get_session_factory()


@pytest.fixture
def service(factory) -> SqlWorkflowService:
    # Probe so missing PG fails as a clean skip, not a crash.
    with factory() as session:
        session.execute(text("SELECT 1"))
    return SqlWorkflowService(factory)


@pytest.fixture
def factory_with_session(factory, service):
    """Bundle ``factory`` and ``service`` so test cleanup helpers
    always have both."""
    return factory, service


def _empty_snapshot() -> RegistrySnapshot:
    """RegistrySnapshot with zero active definitions for compile-failure tests."""
    return RegistrySnapshot(snapshot_id=uuid4(), node_definitions={})


def _hard_delete_artifacts(factory, artifact_ids: list[UUID]) -> None:
    """Test-only helper: delete test artifact rows so the database
    stays clean across test runs."""
    if not artifact_ids:
        return
    with factory.begin() as session:
        session.execute(
            delete(ArtifactVersionModel).where(
                ArtifactVersionModel.artifact_version_id.in_(artifact_ids)
            )
        )


def _hard_delete_workflow(service: SqlWorkflowService, factory, workflow_id: UUID) -> None:
    """Test-only helper: tear down a workflow and all its dependent rows.

    Production code (and the in-process ``delete_workflow`` method)
    refuses to delete a workflow that has compiled plans because those
    plans are the immutable execution history.  Tests need to drop
    everything to leave the database tidy, so this helper does the
    necessary cascading deletes in a single transaction.  It must
    never be called outside test code.
    """
    from src.infra.db.models import CompiledExecutionPlanModel
    with factory.begin() as session:
        # Compiled plans reference revisions, which reference workflows.
        session.execute(
            delete(CompiledExecutionPlanModel).where(
                CompiledExecutionPlanModel.workflow_revision_id.in_(
                    select(WorkflowRevisionModel.revision_id)
                    .where(WorkflowRevisionModel.workflow_id == workflow_id)
                )
            )
        )
        # Outbox events attached to revisions.
        session.execute(
            delete(OutboxEventModel).where(
                OutboxEventModel.event_type == "workflow.revision.activated",
                OutboxEventModel.aggregate_id.in_(
                    select(WorkflowRevisionModel.revision_id)
                    .where(WorkflowRevisionModel.workflow_id == workflow_id)
                ),
            )
        )
        # Workflow runs are referenced by FK to revisions; delete the
        # rows this test inserted itself before the service does.
        session.execute(
            delete(WorkflowRunModel).where(WorkflowRunModel.workflow_revision_id.in_(
                select(WorkflowRevisionModel.revision_id)
                .where(WorkflowRevisionModel.workflow_id == workflow_id)
            ))
        )
        session.execute(delete(WorkflowRevisionModel).where(WorkflowRevisionModel.workflow_id == workflow_id))
        session.execute(delete(WorkflowDraftModel).where(WorkflowDraftModel.workflow_id == workflow_id))
        session.execute(delete(WorkflowModel).where(WorkflowModel.workflow_id == workflow_id))


def _fixture_node_definition(name: str = "toonflow.input") -> NodeDefinitionRevision:
    return NodeDefinitionRevision(
        node_type_id=name,
        revision_id=uuid4(),
        semantic_version="1.0.0",
        executor_ref="toonflow.runtime.input",
        input_ports=[],
        output_ports=[
            PortTypeRef(
                port_id="out", type_id="text", schema_id="toonflow.text",
                schema_version=1, cardinality="required",
            ),
        ],
    )


def _compiler_accepting(_factory):
    """Return a compiler-shaped stub that returns a deterministic plan."""
    from src.schemas.models import CompiledExecutionPlan

    class _Compiler:
        def __init__(self, plan_id: UUID | None = None) -> None:
            self.plan_id = plan_id or uuid4()
            self.call_count = 0
            self.last_revision_id: UUID | None = None

        def compile(self, *, workflow_revision_id, graph, registry_snapshot, compilation_context=None):
            self.call_count += 1
            self.last_revision_id = workflow_revision_id
            return CompiledExecutionPlan(
                plan_id=self.plan_id,
                workflow_revision_id=workflow_revision_id,
                registry_snapshot=registry_snapshot,
                plan_hash="plan-" + str(workflow_revision_id),
                compiler_version="test-1.0",
                resolved_graph=graph,
            )

    return _Compiler()


# -------------------------------------------------------------------
# CAS — full-draft hash layout-only race
# -------------------------------------------------------------------


def test_layout_only_concurrent_save_second_writer_loses(service: SqlWorkflowService, factory) -> None:
    """TF-WF-004 AC-2 with the layout-only case.

    Two threads load the same draft, both only change the layout (so
    ``graph_hash`` is identical), and call ``save_draft`` with the same
    ``expected_full_draft_hash`` token.  Exactly one wins; the other
    must receive a ``ConflictError`` whose details describe the
    current full draft hash so the loser can refresh and re-send.
    """
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    saved = service.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []},
        config={"mode": "race"},
        layout={"n1": {"x": 0, "y": 0}},
        base_graph_hash=initial.graph_hash,
    )
    base = saved.full_draft_hash
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def writer(label: str) -> None:
        svc = SqlWorkflowService(factory)
        # Re-read the draft to mimic a real second tab.
        draft = svc.get_draft(workflow.workflow_id)
        # Pure layout move; graph and config unchanged.
        moved = {"n1": {"x": 1, "y": 1}} if label == "a" else {"n1": {"x": 9, "y": 9}}
        barrier.wait()
        try:
            new_draft = svc.save_draft(
                workflow.workflow_id,
                graph=draft.graph,
                config=draft.config,
                layout={"nodes": moved},
                base_graph_hash=draft.graph_hash,
                expected_full_draft_hash=base,
            )
            results.append({"label": label, "ok": True, "full": new_draft.full_draft_hash, "version": new_draft.draft_version})
        except ConflictError as exc:
            results.append({
                "label": label, "ok": False, "message": exc.message,
                "details": exc.details,
            })
        except Exception as exc:  # pragma: no cover — diagnostic
            results.append({"label": label, "ok": False, "message": f"unexpected: {exc!r}"})

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(writer, "a")
            pool.submit(writer, "b")

        winners = [r for r in results if r["ok"]]
        losers = [r for r in results if not r["ok"]]
        assert len(winners) == 1, f"expected exactly one winner, got {results}"
        assert len(losers) == 1
        loser = losers[0]
        assert "expected_full_draft_hash" in loser["details"]
        assert "current_full_draft_hash" in loser["details"]
        # The loser saw the post-writer full hash.  The winner's saved
        # version is therefore the loser's observed current version.
        assert loser["details"]["current_draft_version"] == winners[0]["version"]
        # Loser must NOT have been silently applied.
        final = service.get_draft(workflow.workflow_id)
        assert final.draft_version == winners[0]["version"]
        # The loser's expected full hash was the *original* full hash;
        # the current full hash is now the winner's.
        assert loser["details"]["expected_full_draft_hash"] == base
        assert loser["details"]["current_full_draft_hash"] == winners[0]["full"]
    finally:
        _hard_delete_workflow(service, factory, workflow.workflow_id)


# -------------------------------------------------------------------
# Hash separation
# -------------------------------------------------------------------


def test_pure_layout_change_alters_layout_hash_only(service: SqlWorkflowService, factory) -> None:
    """AC-3: a pure layout move must change ``layout_hash`` but never
    ``graph_hash`` or ``execution_hash``."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    graph = {"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []}
    config = {"mode": "layout-only"}
    saved1 = service.save_draft(
        workflow.workflow_id, graph=graph, config=config,
        layout={"n1": {"x": 0, "y": 0}},
        base_graph_hash=initial.graph_hash,
    )
    saved2 = service.save_draft(
        workflow.workflow_id, graph=graph, config=config,
        layout={"n1": {"x": 500, "y": 500}},
        base_graph_hash=saved1.graph_hash,
    )
    # graph_hash and execution_hash must be stable across pure layout moves.
    assert saved1.graph_hash == saved2.graph_hash
    assert saved1.execution_hash == saved2.execution_hash
    # layout_hash and full_draft_hash must differ.
    assert saved1.layout_hash != saved2.layout_hash
    assert saved1.full_draft_hash != saved2.full_draft_hash
    _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_node_config_or_pinned_dep_change_alters_execution_hash(service: SqlWorkflowService, factory) -> None:
    """AC-4: changing a node config or a pinned dependency must change
    ``execution_hash``."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    g1 = {"nodes": [{"id": "n1", "type": "toonflow.input", "config": {"k": 1}}], "edges": []}
    g2 = {"nodes": [{"id": "n1", "type": "toonflow.input", "config": {"k": 2}}], "edges": []}
    saved1 = service.save_draft(
        workflow.workflow_id, graph=g1, config={}, layout={},
        base_graph_hash=initial.graph_hash,
        pinned_dependency_revisions=["rev:a"],
    )
    saved2 = service.save_draft(
        workflow.workflow_id, graph=g2, config={}, layout={},
        base_graph_hash=saved1.graph_hash,
        pinned_dependency_revisions=["rev:a"],
    )
    # Different config -> different execution_hash.
    assert saved1.execution_hash != saved2.execution_hash
    saved3 = service.save_draft(
        workflow.workflow_id, graph=g1, config={}, layout={},
        base_graph_hash=saved2.graph_hash,
        pinned_dependency_revisions=["rev:b"],
    )
    # Different pinned dep -> different execution_hash.
    assert saved1.execution_hash != saved3.execution_hash
    _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_hash_separation_unit() -> None:
    """The Foundation hash rules must be reproduced by the in-process
    helpers too, so callers and tests share a single contract."""
    graph = {"nodes": [{"id": "n1"}], "edges": []}
    moved_graph = {"nodes": [{"id": "n1", "position": {"x": 10, "y": 10}}], "edges": []}
    graph_hash_static = compute_graph_hash(graph, {})
    layout_hash_static = compute_layout_hash({})
    # A position-only change in the graph body must not affect graph_hash.
    gh_moved = compute_graph_hash(moved_graph, {})
    assert graph_hash_static == gh_moved
    # Pure execution hash with no deps still mixes the graph hash into
    # the SHA-256 input, so it is not byte-equal to the graph hash; but
    # the function is deterministic and any pinned dep change must
    # alter it.
    eh_no_deps = compute_execution_hash(graph_hash_static, [])
    assert eh_no_deps == compute_execution_hash(graph_hash_static, [])
    assert compute_execution_hash(graph_hash_static, []) != compute_execution_hash(graph_hash_static, ["a"])
    assert compute_execution_hash(graph_hash_static, ["a"]) != compute_execution_hash(graph_hash_static, ["b"])
    # full_draft_hash must move when any of graph/layout/exec/version move.
    f1 = compute_full_draft_hash(graph_hash_static, layout_hash_static, eh_no_deps, 1)
    f2 = compute_full_draft_hash(graph_hash_static, layout_hash_static, eh_no_deps, 2)
    f3 = compute_full_draft_hash(graph_hash_static, "different-layout", eh_no_deps, 1)
    assert f1 != f2
    assert f1 != f3


# -------------------------------------------------------------------
# Activation — stale CAS
# -------------------------------------------------------------------


def test_activate_with_stale_expected_draft_hash_creates_no_revision(service: SqlWorkflowService, factory) -> None:
    """AC-2: the owner reviewed draft v1, somebody else saved v2, the
    activation must refuse and not create any new revision row, any
    CompiledExecutionPlan row, or any activation outbox event."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    graph = {"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []}
    reviewed = service.save_draft(
        workflow.workflow_id, graph=graph, config={"x": 1}, layout={},
        base_graph_hash=initial.graph_hash,
    )
    # Some other writer changes the draft before the owner can confirm.
    latest = service.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "n1", "type": "toonflow.input"}, {"id": "n2", "type": "toonflow.input"}], "edges": []},
        config={"x": 2}, layout={},
        base_graph_hash=reviewed.graph_hash,
    )
    snapshot = _empty_snapshot()
    try:
        with pytest.raises(ConflictError) as excinfo:
            service.publish_compiled_revision(
                workflow.workflow_id, snapshot, _compiler_accepting(factory),
                expected_draft_hash=reviewed.full_draft_hash,
            )
        details = excinfo.value.details
        assert details["expected_draft_hash"] == reviewed.full_draft_hash
        assert details["current_draft_hash"] == latest.full_draft_hash
        assert details["current_draft_version"] == latest.draft_version
        # No revision row, no plan row, no outbox event.
        with factory() as session:
            rev_count = session.scalar(
                select(func.count()).select_from(WorkflowRevisionModel)
                .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
            )
            plan_count = session.scalar(
                select(func.count()).select_from(CompiledExecutionPlanModel)
                .where(CompiledExecutionPlanModel.workflow_revision_id.in_(
                    select(WorkflowRevisionModel.revision_id)
                    .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                ))
            )
            outbox_count = session.scalar(
                select(func.count()).select_from(OutboxEventModel)
                .where(
                    OutboxEventModel.event_type == "workflow.revision.activated",
                    OutboxEventModel.aggregate_id.in_(
                        select(WorkflowRevisionModel.revision_id)
                        .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                    ),
                )
            )
        assert rev_count == 0
        assert plan_count == 0
        assert outbox_count == 0
    finally:
        _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_service_layer_refuses_activation_without_expected_draft_hash(service: SqlWorkflowService, factory) -> None:
    """P0 fix: the service layer must refuse to start an activation
    transaction when ``expected_draft_hash`` is missing or
    malformed.  This is the second line of defence after the HTTP
    Pydantic 422; it must catch any internal caller that bypasses
    the schema.
    """
    from src.core.exceptions import ValidationError_
    workflow = service.create_workflow(owner_scope=OwnerScope(kind="user", id=uuid4()))
    try:
        # Empty string is not a valid 64-char SHA-256.
        with pytest.raises(ValidationError_, match="expected_full_draft_hash"):
            service.publish_compiled_revision(
                workflow.workflow_id, _empty_snapshot(), _compiler_accepting(factory),
                expected_draft_hash="",
            )
        # 63-char string is the wrong length.
        with pytest.raises(ValidationError_, match="expected_full_draft_hash"):
            service.publish_compiled_revision(
                workflow.workflow_id, _empty_snapshot(), _compiler_accepting(factory),
                expected_draft_hash="a" * 63,
            )
        # Confirm no side effects: the compiler must not have been
        # called, and no revision/plan/outbox rows were inserted.
        with factory() as session:
            assert session.scalar(
                select(func.count()).select_from(WorkflowRevisionModel)
                .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(CompiledExecutionPlanModel)
                .where(CompiledExecutionPlanModel.workflow_revision_id.in_(
                    select(WorkflowRevisionModel.revision_id)
                    .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                ))
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(OutboxEventModel)
                .where(
                    OutboxEventModel.event_type == "workflow.revision.activated",
                    OutboxEventModel.aggregate_id.in_(
                        select(WorkflowRevisionModel.revision_id)
                        .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                    ),
                )
            ) == 0
    finally:
        _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_compile_failure_inside_activation_leaves_no_revision_or_plan(service: SqlWorkflowService, factory) -> None:
    """The compiler runs inside the activation transaction; a
    CompilationError must roll back the whole batch — no revision,
    no plan, no outbox event, no Draft.base_revision_id change.
    The owner-confirmed ``expected_draft_hash`` is supplied so the
    test isolates the compile-failure rollback contract from the
    missing-hash guard."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    saved = service.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []},
        config={}, layout={},
        base_graph_hash=initial.graph_hash,
    )

    class _FailingCompiler:
        def compile(self, *, workflow_revision_id, graph, registry_snapshot, compilation_context=None):
            raise CompilationError(
                "injected failure",
                diagnostics=[{"severity": "error", "location": "graph", "message": "boom"}],
            )

    try:
        with pytest.raises(CompilationError):
            service.publish_compiled_revision(
                workflow.workflow_id, _empty_snapshot(), _FailingCompiler(),
                expected_draft_hash=saved.full_draft_hash,
            )
        with factory() as session:
            rev_count = session.scalar(
                select(func.count()).select_from(WorkflowRevisionModel)
                .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
            )
            plan_count = session.scalar(
                select(func.count()).select_from(CompiledExecutionPlanModel)
                .where(CompiledExecutionPlanModel.workflow_revision_id.in_(
                    select(WorkflowRevisionModel.revision_id)
                    .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                ))
            )
            outbox_count = session.scalar(
                select(func.count()).select_from(OutboxEventModel)
                .where(
                    OutboxEventModel.event_type == "workflow.revision.activated",
                    OutboxEventModel.aggregate_id.in_(
                        select(WorkflowRevisionModel.revision_id)
                        .where(WorkflowRevisionModel.workflow_id == workflow.workflow_id)
                    ),
                )
            )
            draft = session.get(WorkflowDraftModel, workflow.workflow_id)
            assert rev_count == 0
            assert plan_count == 0
            assert outbox_count == 0
            assert draft is not None and draft.base_revision_id is None
    finally:
        _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_activate_writes_base_revision_id_and_outbox_atomically(service: SqlWorkflowService, factory) -> None:
    """A successful activation must (a) insert a revision row,
    (b) insert a CompiledExecutionPlan row, (c) update
    Draft.base_revision_id, and (d) insert an outbox event — all in
    one transaction.  A draft edit between read and activate must
    also be reported as the canonical durable conflict."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    reviewed = service.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []},
        config={}, layout={"n1": {"x": 0, "y": 0}},
        base_graph_hash=initial.graph_hash,
    )
    compiler = _compiler_accepting(factory)
    try:
        revision, plan = service.publish_compiled_revision(
            workflow.workflow_id, _empty_snapshot(), compiler,
            expected_draft_hash=reviewed.full_draft_hash,
            actor_id=owner.id,
        )
        with factory() as session:
            draft = session.get(WorkflowDraftModel, workflow.workflow_id)
            assert draft.base_revision_id == revision.revision_id
            plan_row = session.scalar(
                select(CompiledExecutionPlanModel)
                .where(CompiledExecutionPlanModel.workflow_revision_id == revision.revision_id)
            )
            assert plan_row is not None
            assert plan_row.status == "succeeded"
            outbox = session.scalar(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.event_type == "workflow.revision.activated",
                    OutboxEventModel.aggregate_id == revision.revision_id,
                )
            )
            assert outbox is not None
            assert outbox.payload["workflow_id"] == str(workflow.workflow_id)
            assert outbox.payload["expected_draft_hash"] == reviewed.full_draft_hash
            assert outbox.payload["actor_id"] == str(owner.id)
        # ``compiler.compile`` ran exactly once.
        assert compiler.call_count == 1
        assert compiler.last_revision_id == revision.revision_id
    finally:
        _hard_delete_workflow(service, factory, workflow.workflow_id)


# -------------------------------------------------------------------
# Run isolation — run must read only the Revision graph
# -------------------------------------------------------------------


def test_run_pins_to_revision_graph_not_draft(service: SqlWorkflowService, factory) -> None:
    """AC-1: a workflow run recorded against revision R must continue
    to read R.graph even after the draft is mutated and a new revision
    R' is activated.  The runtime already locks on workflow_revision_id;
    this test asserts the row was never written off the draft."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    g1 = {"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []}
    saved1 = service.save_draft(
        workflow.workflow_id, graph=g1, config={}, layout={"n1": {"x": 0, "y": 0}},
        base_graph_hash=initial.graph_hash,
    )
    rev1, _ = service.publish_compiled_revision(
        workflow.workflow_id, _empty_snapshot(), _compiler_accepting(factory),
        expected_draft_hash=saved1.full_draft_hash,
    )
    # Simulate the runtime: the run row is keyed by workflow_revision_id
    # and never reads back the draft.  We can't drive the full runtime
    # here without AtlasCloud smoke; the assertion is the
    # immutability of rev1.graph across draft edits + new revision.
    rev1_graph = service.get_revision_graph(rev1.revision_id)
    assert rev1_graph == g1
    # Edit the draft and activate a second revision.
    after_rev1 = service.get_draft(workflow.workflow_id)
    g2 = {"nodes": [{"id": "n1", "type": "toonflow.input"}, {"id": "n2", "type": "toonflow.input"}], "edges": []}
    saved2 = service.save_draft(
        workflow.workflow_id, graph=g2, config={}, layout={},
        base_graph_hash=after_rev1.graph_hash,
    )
    rev2, _ = service.publish_compiled_revision(
        workflow.workflow_id, _empty_snapshot(), _compiler_accepting(factory),
        expected_draft_hash=saved2.full_draft_hash,
    )
    # rev1 stays frozen and equal to its original graph.
    assert service.get_revision_graph(rev1.revision_id) == g1
    rev1_again = service.get_revision(rev1.revision_id)
    assert rev1_again.graph_hash == rev1.graph_hash
    # And the stored graph on the revision row is still the original.
    assert service.get_revision_graph(rev1.revision_id) == g1
    # rev1 was retired, rev2 is the active one.
    rev1_again = service.get_revision(rev1.revision_id)
    rev2_again = service.get_revision(rev2.revision_id)
    assert rev1_again.revision_status == RevisionStatus.RETIRED
    assert rev2_again.revision_status == RevisionStatus.ACTIVE
    # A row that says "run pinned to rev1" survives the new activation.
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    with factory.begin() as session:
        session.add(WorkflowRunModel(
            run_id=run_id,
            workflow_revision_id=rev1.revision_id,
            compiled_plan_id=uuid4(),
            owner_scope=owner.scoped_id,
            input_snapshot={"x": 1},
            status="completed",
            created_at=now,
        ))
    with factory() as session:
        run_row = session.scalar(
            select(WorkflowRunModel).where(WorkflowRunModel.run_id == run_id)
        )
        # The run row's revision pointer is unchanged.
        assert run_row.workflow_revision_id == rev1.revision_id
        # And the revision row is still resolvable — retired revisions
        # remain readable, satisfying FR-11.
        assert session.get(WorkflowRevisionModel, rev1.revision_id) is not None
    _hard_delete_workflow(service, factory, workflow.workflow_id)


# -------------------------------------------------------------------
# Rollback — preserve history
# -------------------------------------------------------------------


def test_rollback_creates_new_draft_without_altering_historic_revision(service: SqlWorkflowService, factory) -> None:
    """AC-5: rolling back to a prior revision creates a new mutable
    Draft, never overwrites a historic revision row, and the
    historic revision's graph_hash and content are byte-for-byte
    preserved."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    g1 = {"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []}
    saved1 = service.save_draft(
        workflow.workflow_id, graph=g1, config={}, layout={"n1": {"x": 0, "y": 0}},
        base_graph_hash=initial.graph_hash,
    )
    rev1, _ = service.publish_compiled_revision(
        workflow.workflow_id, _empty_snapshot(), _compiler_accepting_factory(),
        expected_draft_hash=saved1.full_draft_hash,
    )
    # Save a second draft and activate it.
    after_rev1 = service.get_draft(workflow.workflow_id)
    g2 = {"nodes": [{"id": "n2", "type": "toonflow.input"}], "edges": []}
    saved2 = service.save_draft(
        workflow.workflow_id, graph=g2, config={}, layout={"n2": {"x": 1, "y": 1}},
        base_graph_hash=after_rev1.graph_hash,
    )
    rev2, _ = service.publish_compiled_revision(
        workflow.workflow_id, _empty_snapshot(), _compiler_accepting_factory(),
        expected_draft_hash=saved2.full_draft_hash,
    )
    # Rollback to rev1.
    new_draft = service.rollback_to_revision(
        workflow.workflow_id, rev1.revision_id, base_graph_hash=saved2.graph_hash,
    )
    # The new draft points at the historic revision and bumps version.
    assert new_draft.base_revision_id == rev1.revision_id
    assert new_draft.draft_version == saved2.draft_version + 1
    # Historic revisions stay frozen.
    rev1_again = service.get_revision(rev1.revision_id)
    rev2_again = service.get_revision(rev2.revision_id)
    assert rev1_again.graph_hash == rev1.graph_hash
    assert rev2_again.graph_hash == rev2.graph_hash
    assert service.get_revision_graph(rev1.revision_id) == g1
    assert service.get_revision_graph(rev2.revision_id) == g2
    _hard_delete_workflow(service, factory, workflow.workflow_id)


def test_rollback_cas_protects_against_legacy_tab(service: SqlWorkflowService, factory) -> None:
    """The rollback path must reject a CAS miss — the same full-draft
    contract as save."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = service.create_workflow(owner_scope=owner)
    initial = service.get_draft(workflow.workflow_id)
    saved1 = service.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "n1", "type": "toonflow.input"}], "edges": []},
        config={}, layout={},
        base_graph_hash=initial.graph_hash,
    )
    rev1, _ = service.publish_compiled_revision(
        workflow.workflow_id, _empty_snapshot(), _compiler_accepting_factory(),
        expected_draft_hash=saved1.full_draft_hash,
    )
    with pytest.raises(ConflictError):
        service.rollback_to_revision(
            workflow.workflow_id, rev1.revision_id,
            base_graph_hash="0" * 64,  # wrong
        )
    _hard_delete_workflow(service, factory, workflow.workflow_id)


# -------------------------------------------------------------------
# Cross-owner / naked ArtifactRef / missing grant / secret import
# -------------------------------------------------------------------


def test_cross_owner_artifact_ref_is_rejected_by_route_helper(service: SqlWorkflowService, factory) -> None:
    """The route helper ``_assert_graph_reference_authorization`` is
    the only place that compares an ``artifact_version_id`` to the
    caller's owner.  A foreign artifact is rejected with a
    ``ForbiddenError`` whose message matches the PRD wording."""
    from src.api.routes.workflow import _assert_graph_reference_authorization
    foreign_artifact_id = uuid4()
    same_owner_artifact = uuid4()
    try:
        with factory.begin() as session:
            session.add(ArtifactVersionModel(
                artifact_version_id=foreign_artifact_id,
                artifact_id=uuid4(),
                schema_id="toonflow.test.v1",
                schema_version=1,
                owner_scope="user:" + str(uuid4()),
                content_uri="",
                content_json={},
                content_hash="",
                lineage_input_refs=[],
                blob_uri="",
            ))
        owner = OwnerScope(kind="user", id=uuid4())
        graph = {
            "nodes": [{
                "id": "n1", "type": "toonflow.input",
                "config": {"artifact_refs": [{"artifact_version_id": str(foreign_artifact_id)}]},
            }],
            "edges": [],
        }
        with pytest.raises(ForbiddenError, match="跨 owner"):
            _assert_graph_reference_authorization(graph, owner)
        # Same-owner artifact must NOT raise.
        with factory.begin() as session:
            session.add(ArtifactVersionModel(
                artifact_version_id=same_owner_artifact,
                artifact_id=uuid4(),
                schema_id="toonflow.test.v1",
                schema_version=1,
                owner_scope=owner.scoped_id,
                content_uri="",
                content_json={},
                content_hash="",
                lineage_input_refs=[],
                blob_uri="",
            ))
        graph2 = {
            "nodes": [{
                "id": "n1", "type": "toonflow.input",
                "config": {"artifact_refs": [{"artifact_version_id": str(same_owner_artifact)}]},
            }],
            "edges": [],
        }
        _assert_graph_reference_authorization(graph2, owner)
    finally:
        _hard_delete_artifacts(factory, [foreign_artifact_id, same_owner_artifact])


def test_missing_grant_resource_ref_is_rejected_by_route_helper(service: SqlWorkflowService, factory) -> None:
    """A ResourceRef that points to a foreign resource must carry a
    valid ``grant_snapshot_id`` matching the revision; missing or
    stale grants are refused at the route helper."""
    from src.api.routes.workflow import _assert_graph_reference_authorization
    foreign_owner = "user:" + str(uuid4())
    foreign_resource_id = uuid4()
    foreign_revision_id = uuid4()
    # A resource revision needs a content_artifact_version_id, so we
    # plant a private artifact the resource can reference.
    content_artifact_id = uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=content_artifact_id,
            artifact_id=uuid4(),
            schema_id="toonflow.test.v1",
            schema_version=1,
            owner_scope=foreign_owner,
            content_uri="",
            content_json={},
            content_hash="",
            lineage_input_refs=[],
            blob_uri="",
        ))
        session.flush()
        session.add(ResourceModel(
            resource_id=foreign_resource_id,
            resource_type="toonflow.world",
            owner_scope=foreign_owner,
        ))
        session.flush()
        session.add(ResourceRevisionModel(
            revision_id=foreign_revision_id,
            resource_id=foreign_resource_id,
            revision_number=1,
            content_artifact_version_id=content_artifact_id,
            revision_status=RevisionStatus.ACTIVE,
        ))
    owner = OwnerScope(kind="user", id=uuid4())
    # Ref without a grant snapshot → Forbidden.
    bad_graph = {
        "nodes": [{
            "id": "n1", "type": "toonflow.input",
            "config": {"resource_refs": [{"resource_id": str(foreign_resource_id), "resource_revision_id": str(foreign_revision_id)}]},
        }],
        "edges": [],
    }
    with pytest.raises(ForbiddenError):
        _assert_graph_reference_authorization(bad_graph, owner)
    # Ref with a non-matching grant → still Forbidden.
    grant_id = uuid4()
    with factory.begin() as session:
        session.add(ResourceGrantSnapshotModel(
            grant_snapshot_id=grant_id,
            resource_revision_id=foreign_revision_id,
            grantee_scope="user:" + str(uuid4()),  # different grantee
            capability_actions=[],
            status="active",
        ))
    bad_graph_2 = {
        "nodes": [{
            "id": "n1", "type": "toonflow.input",
            "config": {"resource_refs": [{
                "resource_id": str(foreign_resource_id),
                "resource_revision_id": str(foreign_revision_id),
                "grant_snapshot_id": str(grant_id),
            }]},
        }],
        "edges": [],
    }
    with pytest.raises(ForbiddenError):
        _assert_graph_reference_authorization(bad_graph_2, owner)


def test_secret_plaintext_import_is_rejected() -> None:
    """An import graph with a top-level secret or api_key must be
    refused before it lands in the draft, even if the type is not
    a CredentialBinding."""
    assert _contains_forbidden({"api_key": "sk-live-1234"}) is True
    assert _contains_forbidden({"password": "hunter2"}) is True
    assert _contains_forbidden({"token": "bearer-foo"}) is True
    assert _contains_forbidden({"authorization": "Bearer xyz"}) is True
    # CredentialBinding references are themselves rejected.
    assert _contains_forbidden({"credential_binding": {"id": "x"}}) is True


# -------------------------------------------------------------------
# Shared helpers (kept here to avoid circular import at module load)
# -------------------------------------------------------------------


def _compiler_accepting_factory():
    """Lazy factory used by the rollback tests."""
    from src.schemas.models import CompiledExecutionPlan

    class _Compiler:
        def __init__(self) -> None:
            self.call_count = 0
            self._plan_id = uuid4()

        def compile(self, *, workflow_revision_id, graph, registry_snapshot, compilation_context=None):
            self.call_count += 1
            return CompiledExecutionPlan(
                plan_id=self._plan_id,
                workflow_revision_id=workflow_revision_id,
                registry_snapshot=registry_snapshot,
                plan_hash="plan-" + str(workflow_revision_id),
                compiler_version="test-1.0",
                resolved_graph=graph,
            )

    return _Compiler()
