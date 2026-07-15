"""PostgreSQL acceptance tests for TF-WF-006 transactional runtime state.

Run explicitly with ``TOONFLOW_RUN_PG_TESTS=1`` after ``alembic upgrade head``.
The production unit suite remains independent of a local database.
"""
from __future__ import annotations

import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.domain.provider.atlascloud import AtlasSubmission, AtlasSubmissionUnknown
from src.infra.db.models import (
    ArtifactVersionModel,
    CompiledExecutionPlanModel,
    FoldCheckpointModel,
    ForEachRunModel,
    NodeRunModel,
    NodeRunAttemptModel,
    OutboxEventModel,
    ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    WorkflowTaskBindingModel,
    SubworkflowModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
    WorkflowModel,
    WorkflowRunModel,
    WorkflowRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


def _plan(revision_id: uuid.UUID) -> CompiledExecutionPlan:
    return CompiledExecutionPlan(
        plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={"nodes": [{"id": "generate", "type": "provider"}], "edges": []},
        plan_hash="pg-runtime-test",
    )


def test_provider_result_is_atomic_and_recovers_from_postgres() -> None:
    factory = get_session_factory()
    workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))

    runtime = RuntimeService(session_factory=factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=uuid.uuid4()))
    with factory() as session:
        node_id = session.scalar(
            select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id)
        )
    assert node_id is not None
    with factory() as session:
        attempt_row = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node_id))
    assert attempt_row is not None
    attempt = runtime._attempt_schema(attempt_row)
    provider, dispatch_event = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="pg-test", model_id="model",
        idempotency_key=f"pg-{uuid.uuid4()}", request_body_hash="request",
    )

    # A missing referenced artifact aborts the entire result transaction.
    with pytest.raises(Exception):
        runtime.record_provider_result(
            provider.provider_attempt_id, model_version="1", response_fingerprint="bad",
            output_artifact_version_ids=[uuid.uuid4()], current_epoch=attempt.execution_epoch,
        )
    with factory() as session:
        assert session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id)) is None
        assert session.scalar(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == provider.provider_attempt_id, OutboxEventModel.event_type == "provider.result")) is None
        persisted_attempt = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        assert persisted_attempt is not None and persisted_attempt.status == AttemptStatus.PENDING

    artifacts = [uuid.uuid4(), uuid.uuid4()]
    with factory.begin() as session:
        for artifact_id in artifacts:
            session.add(ArtifactVersionModel(artifact_version_id=artifact_id, owner_scope="user:test"))
    record, result_event = runtime.record_provider_result(
        provider.provider_attempt_id, model_version="1", response_fingerprint="good",
        actual_cost=0.25, output_artifact_version_ids=artifacts,
        current_epoch=attempt.execution_epoch,
    )
    assert result_event.purpose == "result_publish"

    # A new service instance has no process-local state, so this is a real reload check.
    recovered = RuntimeService(session_factory=factory)
    assert recovered.recover()["pending_outbox"] >= 2
    with factory() as session:
        bindings = session.scalars(select(ProviderOutputBindingModel).where(ProviderOutputBindingModel.record_id == record.record_id).order_by(ProviderOutputBindingModel.output_index)).all()
        assert [binding.output_artifact_version_id for binding in bindings] == artifacts
        assert session.get(OutboxEventModel, dispatch_event.event_id) is not None


def test_worker_claims_one_durable_attempt_and_unknown_is_not_blindly_requeued() -> None:
    factory = get_session_factory()
    workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(session_factory=factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=uuid.uuid4()))
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    assert node_id is not None
    with factory() as session:
        attempt_row = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node_id))
    assert attempt_row is not None
    attempt = runtime._attempt_schema(attempt_row)
    worker = RuntimeWorker(factory)
    first = worker.claim_next_attempt("worker-a", run_id=run.run_id)
    second = worker.claim_next_attempt("worker-b", run_id=run.run_id)
    assert first is not None and first.attempt.attempt_id == attempt.attempt_id
    assert second is None

    provider, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="atlascloud", model_id="model",
        idempotency_key=f"unknown-{uuid.uuid4()}", request_body_hash="request",
    )
    task_id = f"atlas-task-{uuid.uuid4()}"
    runtime.bind_provider_task(provider.provider_attempt_id, task_id)
    runtime.mark_provider_unknown(provider.provider_attempt_id)
    report = worker.recover_pending()
    assert report.unknown_attempts >= 1
    with factory() as session:
        persisted_provider = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        binding = session.scalar(select(WorkflowTaskBindingModel).where(
            WorkflowTaskBindingModel.provider_attempt_id == provider.provider_attempt_id,
        ))
        assert persisted_provider is not None and persisted_provider.status == AttemptStatus.UNKNOWN
        assert binding is not None and binding.provider_task_id == task_id


def test_expired_waiting_external_submission_becomes_unknown_without_new_dispatch() -> None:
    factory = get_session_factory()
    workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(session_factory=factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=uuid.uuid4()))
    runtime.start_run(run.run_id)
    worker = RuntimeWorker(factory)
    claim = worker.claim_next_attempt("worker-a", run_id=run.run_id)
    assert claim is not None
    provider, _ = runtime.dispatch_provider(
        claim.attempt.attempt_id, provider_id="atlascloud", model_id="model",
        idempotency_key=f"crash-{uuid.uuid4()}", request_body_hash="request",
    )
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, claim.attempt.attempt_id)
        persisted = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        assert persisted is not None
        persisted.status = AttemptStatus.WAITING_EXTERNAL
        attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        dispatches_before = session.scalar(select(func.count()).select_from(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider.provider_attempt_id,
            OutboxEventModel.purpose == "provider_dispatch",
        ))
    worker.recover_pending()
    with factory() as session:
        attempt = session.get(NodeRunAttemptModel, claim.attempt.attempt_id)
        persisted = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        dispatches_after = session.scalar(select(func.count()).select_from(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider.provider_attempt_id,
            OutboxEventModel.purpose == "provider_dispatch",
        ))
        reconcile = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider.provider_attempt_id,
            OutboxEventModel.purpose == "provider_reconcile",
        ))
        assert attempt is not None and attempt.status == AttemptStatus.UNKNOWN
        assert persisted is not None and persisted.status == AttemptStatus.UNKNOWN
        assert dispatches_after == dispatches_before
        assert reconcile is not None


def test_waiting_external_atlas_task_is_polled_and_duplicate_callback_is_idempotent() -> None:
    factory = get_session_factory()
    workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
    owner_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(session_factory=factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert attempt is not None
    provider, _ = runtime.dispatch_provider(attempt.attempt_id, provider_id="atlascloud", model_id="m", idempotency_key=str(uuid.uuid4()), request_body_hash="h")
    task_id = f"atlas-{uuid.uuid4()}"
    runtime.bind_provider_task(provider.provider_attempt_id, task_id)

    class FakeAtlas:
        def get_prediction(self, requested_task_id: str) -> dict:
            return {"task_id": requested_task_id, "status": "completed", "outputs": [{"text": "done"}], "model_version": "m"}

    worker = RuntimeWorker(factory)
    report = worker.reconcile_unknown(FakeAtlas())  # type: ignore[arg-type]
    assert report.checked >= 1 and report.completed >= 1 and report.failed == 0
    again = worker.ingest_atlas_callback({"task_id": task_id, "status": "completed", "outputs": [{"text": "done"}], "model_version": "m"})
    assert again is not None
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id)))
        bindings = list(session.scalars(select(ProviderOutputBindingModel).where(ProviderOutputBindingModel.record_id == records[0].record_id)))
        assert len(records) == 1
        assert len(bindings) == 1


def test_scheduler_uses_condition_skip_and_join_all_before_creating_attempt() -> None:
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {
        "nodes": [
            {"id": "switch", "type": "condition", "config": {"default_branch": "yes"}},
            {"id": "yes", "type": "provider"}, {"id": "no", "type": "provider"},
            {"id": "join", "type": "join", "config": {"strategy": "all"}},
        ],
        "edges": [
            {"source": "switch", "sourceHandle": "yes", "target": "yes"},
            {"source": "switch", "sourceHandle": "no", "target": "no"},
            {"source": "yes", "target": "join"}, {"source": "no", "target": "join"},
        ],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph=graph, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE))
    plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=graph, plan_hash="scheduler")
    runtime = RuntimeService(session_factory=factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    runtime.start_run(run.run_id)
    with factory() as session:
        nodes = {row.node_instance_id: row for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))}
        switch_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == nodes["switch"].node_run_id))
        assert switch_attempt is not None
        assert nodes["yes"].status == NodeRunStatus.PENDING
    runtime.complete_attempt(switch_attempt.attempt_id, epoch=switch_attempt.execution_epoch)
    with factory() as session:
        nodes = {row.node_instance_id: row for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))}
        yes_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == nodes["yes"].node_run_id))
        assert nodes["no"].status == NodeRunStatus.SKIPPED
        assert yes_attempt is not None
        assert session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == nodes["join"].node_run_id)) is None
    runtime.complete_attempt(yes_attempt.attempt_id, epoch=yes_attempt.execution_epoch)
    with factory() as session:
        nodes = {row.node_instance_id: row for row in session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))}
        assert nodes["join"].status == NodeRunStatus.READY
        assert session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == nodes["join"].node_run_id)) is not None


def test_worker_map_fold_publishes_ordered_lineage_and_fenced_checkpoints() -> None:
    """Map expansion is a runtime path, not control-flow CRUD.

    The worker creates per-item ArtifactVersions and one ordered parent output;
    a stale epoch cannot append a Fold checkpoint.
    """
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {
        "nodes": [{"id": "fold", "type": "fold", "config": {"items": [{"v": 1}, {"v": 2}], "max_items": 2, "max_concurrency": 1}}],
        "edges": [],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph=graph, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE))
    plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=graph, plan_hash="fold")
    runtime, worker = RuntimeService(factory), RuntimeWorker(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    runtime.start_run(run.run_id)
    first = worker.claim_next_map_item("map-worker", run_id=run.run_id)
    assert first is not None and first.item_index == 0
    worker.complete_map_item(first.map_item_id, {"sum": 1}, expected_epoch=1)
    second = worker.claim_next_map_item("map-worker", run_id=run.run_id)
    assert second is not None and second.item_index == 1
    worker.complete_map_item(second.map_item_id, {"sum": 3}, expected_epoch=1)
    with factory() as session:
        flow = session.scalar(select(ForEachRunModel).where(ForEachRunModel.run_id == run.run_id))
        assert flow is not None and flow.status == "completed"
        checkpoints = list(session.scalars(select(FoldCheckpointModel).where(FoldCheckpointModel.for_each_id == flow.for_each_id).order_by(FoldCheckpointModel.item_index)))
        assert [item.item_index for item in checkpoints] == [0, 1]
        parent = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "fold"))
        assert parent is not None and parent.status == NodeRunStatus.COMPLETED
        parent_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == parent.node_run_id))
        assert parent_attempt is not None
        aggregate_id = uuid.UUID(parent_attempt.fixed_input["map_output"]["artifact_version_id"])
        aggregate = session.get(ArtifactVersionModel, aggregate_id)
        assert aggregate is not None and aggregate.created_by_run_id == run.run_id
        assert [ref["item_index"] for ref in aggregate.lineage_input_refs] == [0, 1]


def test_subworkflow_uses_persisted_child_plan_and_timeout_fails_parent() -> None:
    factory = get_session_factory()
    owner_id, parent_workflow, parent_revision, child_workflow, child_revision = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    child_graph = {"nodes": [{"id": "child-work", "type": "provider"}], "edges": []}
    child_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=child_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=child_graph, plan_hash="child-plan")
    parent_graph = {"nodes": [{"id": "call", "type": "subworkflow_call", "config": {
        "workflow_revision_id": str(child_revision), "depth": 1, "max_depth": 2, "max_child_nodes": 10,
        "input_mapping": {}, "output_mapping": {}, "timeout_seconds": 1,
    }}], "edges": []}
    with factory.begin() as session:
        session.add_all([
            WorkflowModel(workflow_id=parent_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowModel(workflow_id=child_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowRevisionModel(revision_id=parent_revision, workflow_id=parent_workflow, revision_number=1,
                graph=parent_graph, graph_hash="pg", execution_hash="pe", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE),
            WorkflowRevisionModel(revision_id=child_revision, workflow_id=child_workflow, revision_number=1,
                graph=child_graph, graph_hash="cg", execution_hash="ce", registry_snapshot_id=child_plan.registry_snapshot.snapshot_id, revision_status=RevisionStatus.ACTIVE),
        ])
        session.flush()
        session.add(CompiledExecutionPlanModel(plan_id=child_plan.plan_id, workflow_revision_id=child_revision,
            registry_snapshot_id=child_plan.registry_snapshot.snapshot_id, status="succeeded", plan_hash=child_plan.plan_hash,
            compiler_version="1", plan_json=child_plan.model_dump(mode="json"), diagnostics=[]))
    parent_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=parent_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=parent_graph, plan_hash="parent-plan")
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=parent_plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        binding = session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == run.run_id))
        assert binding is not None and binding.child_run_id is not None
        child = session.get(WorkflowRunModel, binding.child_run_id)
        assert child is not None and child.compiled_plan_id == child_plan.plan_id
    with factory.begin() as session:
        binding = session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == run.run_id))
        assert binding is not None
        binding.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
    worker = RuntimeWorker(factory)
    assert worker.recover_subworkflow_timeouts() == 1
    with factory() as session:
        binding = session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == run.run_id))
        parent = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "call"))
        assert binding is not None and binding.status == "timed_out"
        assert parent is not None and parent.status == NodeRunStatus.FAILED


def test_subworkflow_rejects_cross_owner_artifact_but_accepts_granted_resource() -> None:
    factory = get_session_factory()
    owner_id, foreign_id, workflow_id, revision_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    artifact_id, resource_id, resource_revision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE))
        session.add(WorkflowRunModel(run_id=run_id, workflow_revision_id=revision_id, compiled_plan_id=uuid.uuid4(), owner_scope=f"user:{owner_id}", input_snapshot={}, status="running"))
        session.add(ArtifactVersionModel(artifact_version_id=artifact_id, artifact_id=uuid.uuid4(), schema_id="asset", schema_version=1,
            owner_scope=f"user:{foreign_id}", content_hash="foreign", content_uri="", blob_uri="", content_json={}))
        session.add(ResourceModel(resource_id=resource_id, resource_type="world", owner_scope=f"user:{foreign_id}"))
        session.flush()
        session.add(ResourceRevisionModel(revision_id=resource_revision_id, resource_id=resource_id, revision_number=1,
            content_artifact_version_id=artifact_id, revision_status=RevisionStatus.ACTIVE))
        session.flush()
        session.add(ResourceGrantSnapshotModel(grant_snapshot_id=uuid.uuid4(), resource_revision_id=resource_revision_id,
            grantee_scope=f"user:{owner_id}", status="active"))
    runtime = RuntimeService(factory)
    typed = {"source_port": "world", "target_port": "world", "schema_id": "world", "schema_version": 1}
    with factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        assert run is not None
        with pytest.raises(Exception):
            runtime._sql_validate_subworkflow_inputs(session, run, {"input_mapping": {"bad": {**typed, "artifact_version_id": str(artifact_id)}}})
        runtime._sql_validate_subworkflow_inputs(session, run, {"input_mapping": {"ok": {**typed, "resource_revision_id": str(resource_revision_id)}}})


def test_subworkflow_executes_typed_mapping_and_returns_pinned_child_output() -> None:
    """A granted Resource reaches a child run and typed output is pinned back to its parent."""
    factory = get_session_factory()
    owner_id, foreign_id = uuid.uuid4(), uuid.uuid4()
    parent_workflow, child_workflow = uuid.uuid4(), uuid.uuid4()
    parent_revision, child_revision = uuid.uuid4(), uuid.uuid4()
    foreign_artifact, resource_id, resource_revision_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    parent_snapshot, child_snapshot = uuid.uuid4(), uuid.uuid4()
    input_mapping = {
        "world": {"source_port": "world", "target_port": "world", "schema_id": "world", "schema_version": 1,
                  "resource_revision_id": str(resource_revision_id)},
    }
    output_mapping = {
        "result": {"source_port": "result", "target_port": "result", "schema_id": "shot", "schema_version": 1,
                   "source_node_id": "child-generate"},
    }
    child_graph = {"nodes": [{"id": "child-generate", "type": "provider"}], "edges": []}
    parent_graph = {"nodes": [{"id": "call", "type": "subworkflow_call", "config": {
        "workflow_revision_id": str(child_revision), "depth": 1, "max_depth": 2, "max_child_nodes": 10,
        "budget_limit": 4, "input_mapping": input_mapping, "output_mapping": output_mapping,
    }}], "edges": []}
    parent_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=parent_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=parent_snapshot), resolved_graph=parent_graph,
        capability_snapshots=["atlas.llm"], budget_limits={"max_cost": 5}, plan_hash="parent-typed")
    child_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=child_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=child_snapshot), resolved_graph=child_graph,
        capability_snapshots=["atlas.llm"], budget_limits={"max_cost": 4}, plan_hash="child-typed")
    with factory.begin() as session:
        session.add_all([
            WorkflowModel(workflow_id=parent_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowModel(workflow_id=child_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowRevisionModel(revision_id=parent_revision, workflow_id=parent_workflow, revision_number=1, graph=parent_graph,
                graph_hash="pg", execution_hash="pe", registry_snapshot_id=parent_snapshot, revision_status=RevisionStatus.ACTIVE),
            WorkflowRevisionModel(revision_id=child_revision, workflow_id=child_workflow, revision_number=1, graph=child_graph,
                graph_hash="cg", execution_hash="ce", registry_snapshot_id=child_snapshot, revision_status=RevisionStatus.ACTIVE),
            ArtifactVersionModel(artifact_version_id=foreign_artifact, artifact_id=uuid.uuid4(), schema_id="world", schema_version=1,
                owner_scope=f"user:{foreign_id}", content_hash="foreign-world", content_uri="", blob_uri="", content_json={}),
            ResourceModel(resource_id=resource_id, resource_type="world", owner_scope=f"user:{foreign_id}"),
        ])
        session.flush()
        # These ORM models intentionally have no relationships, so FK order
        # must be explicit in a fixture as it is in production repositories.
        session.add(ResourceRevisionModel(revision_id=resource_revision_id, resource_id=resource_id, revision_number=1,
            content_artifact_version_id=foreign_artifact, revision_status=RevisionStatus.ACTIVE))
        session.flush()
        session.add_all([
            ResourceGrantSnapshotModel(grant_snapshot_id=uuid.uuid4(), resource_revision_id=resource_revision_id,
                grantee_scope=f"user:{owner_id}", status="active"),
            CompiledExecutionPlanModel(plan_id=parent_plan.plan_id, workflow_revision_id=parent_revision,
                registry_snapshot_id=parent_snapshot, status="succeeded", plan_hash=parent_plan.plan_hash,
                compiler_version="1", plan_json=parent_plan.model_dump(mode="json"), diagnostics=[]),
            CompiledExecutionPlanModel(plan_id=child_plan.plan_id, workflow_revision_id=child_revision,
                registry_snapshot_id=child_snapshot, status="succeeded", plan_hash=child_plan.plan_hash,
                compiler_version="1", plan_json=child_plan.model_dump(mode="json"), diagnostics=[]),
        ])
    runtime = RuntimeService(factory)
    # This travels through the same child-run materialisation path as a real
    # request.  A bare foreign ArtifactVersion is never a substitute for a
    # fixed, granted ResourceRevision.
    denied_graph = deepcopy(parent_graph)
    denied_graph["nodes"][0]["config"]["input_mapping"] = {
        "world": {"source_port": "world", "target_port": "world", "schema_id": "world", "schema_version": 1,
                  "artifact_version_id": str(foreign_artifact)},
    }
    denied_plan = parent_plan.model_copy(update={"resolved_graph": denied_graph})
    with pytest.raises(Exception, match="Cross-owner ArtifactVersion"):
        runtime.create_run(compiled_plan=denied_plan, owner_scope=OwnerScope(kind="user", id=owner_id), input_snapshot={"budget_limit": 5})
    parent_run = runtime.create_run(compiled_plan=parent_plan, owner_scope=OwnerScope(kind="user", id=owner_id), input_snapshot={"budget_limit": 5})
    with factory() as session:
        binding = session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == parent_run.run_id))
        assert binding is not None and binding.child_run_id is not None
        child_attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == binding.child_run_id))
        assert child_attempt is not None
    provider, _ = runtime.dispatch_provider(child_attempt.attempt_id, provider_id="atlascloud", model_id="atlas-llm",
        idempotency_key=f"child-{uuid.uuid4()}", request_body_hash="child-request")
    output_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(artifact_version_id=output_id, artifact_id=uuid.uuid4(), schema_id="shot", schema_version=1,
            owner_scope=f"user:{owner_id}", content_hash="child-output", content_uri="", blob_uri="", content_json={}))
    runtime.record_provider_result(provider.provider_attempt_id, model_version="1", response_fingerprint="child-output",
        output_artifact_version_ids=[output_id], current_epoch=child_attempt.execution_epoch)
    with factory() as session:
        binding = session.scalar(select(SubworkflowModel).where(SubworkflowModel.run_id == parent_run.run_id))
        parent_node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == parent_run.run_id, NodeRunModel.node_instance_id == "call"))
        parent_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == parent_node.node_run_id))
        assert binding is not None and binding.status == "completed"
        assert parent_node is not None and parent_node.status == NodeRunStatus.COMPLETED
        assert parent_attempt is not None and parent_attempt.fixed_input["subworkflow_output"]["result"] == [str(output_id)]


def test_subworkflow_rejects_capability_expansion_and_budget_increase() -> None:
    factory = get_session_factory()
    owner_id, parent_workflow, child_workflow = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    parent_revision, child_revision = uuid.uuid4(), uuid.uuid4()
    parent_snapshot, child_snapshot = uuid.uuid4(), uuid.uuid4()
    child_graph = {"nodes": [{"id": "work", "type": "provider"}], "edges": []}
    child_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=child_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=child_snapshot), resolved_graph=child_graph,
        capability_snapshots=["atlas.video"], plan_hash="child-expands")
    config = {"workflow_revision_id": str(child_revision), "depth": 1, "max_depth": 2, "max_child_nodes": 10,
              "budget_limit": 6, "input_mapping": {}, "output_mapping": {}}
    parent_graph = {"nodes": [{"id": "call", "type": "subworkflow_call", "config": config}], "edges": []}
    parent_plan = CompiledExecutionPlan(plan_id=uuid.uuid4(), workflow_revision_id=parent_revision,
        registry_snapshot=RegistrySnapshot(snapshot_id=parent_snapshot), resolved_graph=parent_graph,
        capability_snapshots=["atlas.llm"], plan_hash="parent-restricted")
    with factory.begin() as session:
        session.add_all([
            WorkflowModel(workflow_id=parent_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowModel(workflow_id=child_workflow, owner_scope=f"user:{owner_id}"),
            WorkflowRevisionModel(revision_id=parent_revision, workflow_id=parent_workflow, revision_number=1, graph=parent_graph, graph_hash="p", execution_hash="p", registry_snapshot_id=parent_snapshot, revision_status=RevisionStatus.ACTIVE),
            WorkflowRevisionModel(revision_id=child_revision, workflow_id=child_workflow, revision_number=1, graph=child_graph, graph_hash="c", execution_hash="c", registry_snapshot_id=child_snapshot, revision_status=RevisionStatus.ACTIVE),
        ])
        # No ORM relationships exist between plans and revisions either.
        # Flush the FK targets before the immutable plan rows.
        session.flush()
        session.add_all([
            CompiledExecutionPlanModel(plan_id=parent_plan.plan_id, workflow_revision_id=parent_revision, registry_snapshot_id=parent_snapshot, status="succeeded", plan_hash=parent_plan.plan_hash, compiler_version="1", plan_json=parent_plan.model_dump(mode="json"), diagnostics=[]),
            CompiledExecutionPlanModel(plan_id=child_plan.plan_id, workflow_revision_id=child_revision, registry_snapshot_id=child_snapshot, status="succeeded", plan_hash=child_plan.plan_hash, compiler_version="1", plan_json=child_plan.model_dump(mode="json"), diagnostics=[]),
        ])
    # The stricter capability check runs before a child Run can be inserted.
    with pytest.raises(Exception, match="capability"):
        RuntimeService(factory).create_run(compiled_plan=parent_plan, owner_scope=OwnerScope(kind="user", id=owner_id), input_snapshot={"budget_limit": 5})
    # Once capabilities are a subset, the inherited budget remains an
    # independent hard ceiling and cannot be widened by child config.
    with factory.begin() as session:
        row = session.get(CompiledExecutionPlanModel, child_plan.plan_id)
        assert row is not None
        plan_json = dict(row.plan_json)
        plan_json["capability_snapshots"] = ["atlas.llm"]
        row.plan_json = plan_json
    with pytest.raises(Exception, match="budget"):
        RuntimeService(factory).create_run(compiled_plan=parent_plan, owner_scope=OwnerScope(kind="user", id=owner_id), input_snapshot={"budget_limit": 5})


def test_provider_dispatch_outbox_requires_submission_proof_and_is_idempotent() -> None:
    """An audit event is never a reason to submit an external request again."""
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="dispatch", execution_hash="dispatch", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert attempt is not None
    proven, proven_event = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="atlascloud", model_id="test",
        idempotency_key=f"known-{uuid.uuid4()}", request_body_hash="known",
    )
    runtime.bind_provider_task(proven.provider_attempt_id, f"task-{uuid.uuid4()}")
    worker = RuntimeWorker(factory)
    first = worker.consume_provider_dispatch_outbox(limit=10_000)
    assert first["published"] >= 1
    with factory() as session:
        event = session.get(OutboxEventModel, proven_event.event_id)
        persisted = session.get(ProviderInvocationAttemptModel, proven.provider_attempt_id)
        assert event is not None and event.published_at is not None
        assert persisted is not None and persisted.status != AttemptStatus.UNKNOWN

    # A second run supplies an event but no task-id/result proof.  It must be
    # fenced to UNKNOWN and schedule reconciliation, not a fresh dispatch.
    unproven_run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        unproven_attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == unproven_run.run_id))
        assert unproven_attempt is not None
    unproven, unproven_event = runtime.dispatch_provider(
        unproven_attempt.attempt_id, provider_id="atlascloud", model_id="test",
        idempotency_key=f"unknown-{uuid.uuid4()}", request_body_hash="unknown",
    )
    second = worker.consume_provider_dispatch_outbox(limit=10_000)
    assert second["unknown"] == 1
    assert worker.consume_provider_dispatch_outbox(limit=10_000) == {"published": 0, "unknown": 0}
    with factory() as session:
        event = session.get(OutboxEventModel, unproven_event.event_id)
        provider = session.get(ProviderInvocationAttemptModel, unproven.provider_attempt_id)
        attempt_row = session.get(NodeRunAttemptModel, unproven_attempt.attempt_id)
        reconciliation = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == unproven.provider_attempt_id,
            OutboxEventModel.purpose == "provider_reconcile",
        )))
        dispatches = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == unproven.provider_attempt_id,
            OutboxEventModel.purpose == "provider_dispatch",
        )))
        assert event is not None and event.published_at is not None
        assert provider is not None and provider.status == AttemptStatus.UNKNOWN
        assert attempt_row is not None and attempt_row.status == AttemptStatus.UNKNOWN
        assert len(reconciliation) == 1
        assert len(dispatches) == 1


def test_dispatch_outbox_recovers_frozen_contract_and_unknown_without_task_is_durable() -> None:
    """A crash before send reuses the committed idempotency key; an ambiguous
    send without a prediction id stays in a durable manual reconciliation queue.
    """
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="recover-send", execution_hash="recover-send", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert attempt is not None
        epoch = attempt.execution_epoch
    provider, event = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="atlascloud", model_id="atlas-test",
        idempotency_key=f"recoverable-{uuid.uuid4()}", request_body_hash="frozen",
        dispatch_payload={
            "operation": "llm", "request": {"messages": [{"role": "user", "content": "fixed"}]},
            "expected_epoch": epoch,
            "result_schema": {"schema_id": "test_output", "schema_version": 1, "owner_scope": f"user:{owner_id}"},
        },
    )

    class Success:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def submit(self, *, operation: str, model_id: str, payload: dict, idempotency_key: str) -> AtlasSubmission:
            self.keys.append(idempotency_key)
            assert operation == "llm" and model_id == "atlas-test"
            assert payload == {"messages": [{"role": "user", "content": "fixed"}]}
            return AtlasSubmission(task_id=None, model_version="atlas-test-v1", outputs=[{"text": "done"}], usage={}, actual_cost=0.1, raw_fingerprint="recover")

    adapter = Success()
    assert RuntimeWorker(factory).consume_provider_dispatch_outbox(adapter=adapter) == {"published": 1, "unknown": 0}
    assert adapter.keys == [provider.idempotency_key]
    with factory() as session:
        assert session.get(OutboxEventModel, event.event_id).published_at is not None
        assert session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id)) is not None

    # An uncertain network result has no provider task id. It is intentionally
    # never polled/re-sent, but remains queryable as a pending reconciliation.
    second = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        unknown_attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == second.run_id))
        assert unknown_attempt is not None
    unknown_provider, _ = runtime.dispatch_provider(
        unknown_attempt.attempt_id, provider_id="atlascloud", model_id="atlas-test",
        idempotency_key=f"ambiguous-{uuid.uuid4()}", request_body_hash="frozen-unknown",
        dispatch_payload={
            "operation": "llm", "request": {"messages": []}, "expected_epoch": unknown_attempt.execution_epoch,
            "result_schema": {"schema_id": "test_output", "schema_version": 1, "owner_scope": f"user:{owner_id}"},
        },
    )

    class Unknown:
        def submit(self, **_kwargs: object) -> AtlasSubmission:
            raise AtlasSubmissionUnknown()

    worker = RuntimeWorker(factory)
    assert worker.consume_provider_dispatch_outbox(adapter=Unknown()) == {"published": 0, "unknown": 1}
    report = worker.reconcile_unknown(adapter=Success())  # type: ignore[arg-type]
    assert report.pending >= 1
    with factory() as session:
        persisted = session.get(ProviderInvocationAttemptModel, unknown_provider.provider_attempt_id)
        queue = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == unknown_provider.provider_attempt_id,
            OutboxEventModel.purpose == "provider_reconcile",
            OutboxEventModel.published_at.is_(None),
        ))
        assert persisted is not None and persisted.status == AttemptStatus.UNKNOWN
        assert queue is not None


def test_dispatch_outbox_fences_late_result_for_superseded_epoch() -> None:
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="late", execution_hash="late", registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        old = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert old is not None
    provider, event = runtime.dispatch_provider(
        old.attempt_id, provider_id="atlascloud", model_id="atlas-test", idempotency_key=f"late-{uuid.uuid4()}", request_body_hash="late",
        dispatch_payload={"operation": "llm", "request": {"messages": []}, "expected_epoch": old.execution_epoch,
                          "result_schema": {"schema_id": "test_output", "schema_version": 1, "owner_scope": f"user:{owner_id}"}},
    )
    replacement = runtime.create_attempt(old.node_run_id)

    class Late:
        def submit(self, **_kwargs: object) -> AtlasSubmission:
            return AtlasSubmission(task_id=None, model_version="late", outputs=[{"text": "late"}], usage={}, actual_cost=0, raw_fingerprint="late")

    # The stale event is acknowledged without any provider submit; its epoch
    # fence was committed before the worker acquired the send lease.
    assert RuntimeWorker(factory).consume_provider_dispatch_outbox(adapter=Late()) == {"published": 1, "unknown": 0}
    with factory() as session:
        old_row = session.get(NodeRunAttemptModel, old.attempt_id)
        new_row = session.get(NodeRunAttemptModel, replacement.attempt_id)
        provider_row = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        assert old_row is not None and old_row.status == AttemptStatus.SUPERSEDED
        assert new_row is not None and new_row.execution_epoch == old.execution_epoch + 1
        assert provider_row is not None and provider_row.status == AttemptStatus.SUPERSEDED
        assert session.get(OutboxEventModel, event.event_id).published_at is not None
        assert session.scalar(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id)) is None


def test_cancellation_fences_late_provider_result_and_downstream_scheduling() -> None:
    """AC-4: cancellation is a durable fence, not a UI-only state change."""
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {
        "nodes": [{"id": "source", "type": "provider"}, {"id": "next", "type": "provider"}],
        "edges": [{"source": "source", "target": "next"}],
    }
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph=graph, graph_hash="cancel", execution_hash="cancel",
            registry_snapshot_id=uuid.uuid4(), revision_status=RevisionStatus.ACTIVE,
        ))
    plan = CompiledExecutionPlan(
        plan_id=uuid.uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()), resolved_graph=graph,
        plan_hash="cancel-fence",
    )
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    runtime.start_run(run.run_id)
    with factory() as session:
        source = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "source",
        ))
        assert source is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == source.node_run_id))
        assert attempt is not None
    provider, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="atlascloud", model_id="m",
        idempotency_key=f"cancel-{uuid.uuid4()}", request_body_hash="cancel",
    )
    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(artifact_version_id=artifact_id, owner_scope=f"user:{owner_id}"))
    runtime.cancel_run(run.run_id)
    with pytest.raises(Exception, match="Cancelled or superseded"):
        runtime.dispatch_provider(
            attempt.attempt_id, provider_id="atlascloud", model_id="m",
            idempotency_key=f"post-cancel-{uuid.uuid4()}", request_body_hash="late-dispatch",
        )
    with pytest.raises(Exception, match="Cancelled or superseded"):
        runtime.record_provider_result(
            provider.provider_attempt_id, model_version="m", response_fingerprint="late",
            output_artifact_version_ids=[artifact_id], current_epoch=attempt.execution_epoch,
        )
    with factory() as session:
        persisted_run = session.get(WorkflowRunModel, run.run_id)
        persisted_provider = session.get(ProviderInvocationAttemptModel, provider.provider_attempt_id)
        next_node = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "next",
        ))
        records = session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id,
        ))
        assert persisted_run is not None and persisted_run.status == RunStatus.CANCELLED
        assert persisted_provider is not None and persisted_provider.status == AttemptStatus.CANCELLED
        assert next_node is not None and next_node.status == NodeRunStatus.CANCELLED
        assert records == 0


def test_retry_reuses_fixed_input_supersedes_old_epoch_and_latest_is_a_slice() -> None:
    """AC-2/AC-5: retry preserves inputs; changed inputs require another run."""
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="retry", execution_hash="retry", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    run = runtime.create_run(
        compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id),
        input_snapshot={"prompt": "fixed-v1"},
    )
    runtime.start_run(run.run_id)
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        old = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert old is not None
        old_input = dict(old.fixed_input or {})
    retry = runtime.create_attempt(node.node_run_id)
    with factory() as session:
        old_row = session.get(NodeRunAttemptModel, old.attempt_id)
        retry_row = session.get(NodeRunAttemptModel, retry.attempt_id)
        assert old_row is not None and old_row.status == AttemptStatus.SUPERSEDED
        assert retry_row is not None and retry_row.fixed_input == old_input
        assert retry_row.execution_epoch == old.execution_epoch + 1
        assert session.get(WorkflowRunModel, run.run_id).input_snapshot == {"prompt": "fixed-v1"}


def test_result_outbox_failure_is_retried_without_duplicate_business_record() -> None:
    """AC-6: failed delivery retains the committed result event for recovery."""
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="outbox", execution_hash="outbox", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=_plan(revision_id), owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        attempt = session.scalar(select(NodeRunAttemptModel).join(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert attempt is not None
    provider, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="atlascloud", model_id="m",
        idempotency_key=f"outbox-{uuid.uuid4()}", request_body_hash="outbox",
    )
    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(artifact_version_id=artifact_id, owner_scope=f"user:{owner_id}"))
    record, event = runtime.record_provider_result(
        provider.provider_attempt_id, model_version="m", response_fingerprint="result",
        output_artifact_version_ids=[artifact_id], current_epoch=attempt.execution_epoch,
    )
    worker = RuntimeWorker(factory)
    first = worker.deliver_outbox(
        lambda _event: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
        purposes={"result_publish"}, event_ids={event.event_id},
    )
    assert first == {"delivered": 0, "failed": 1}
    delivered: list[uuid.UUID] = []
    second = worker.deliver_outbox(
        lambda outbox: delivered.append(outbox.event_id), purposes={"result_publish"},
        event_ids={event.event_id},
    )
    assert second == {"delivered": 1, "failed": 0}
    with factory() as session:
        persisted_event = session.get(OutboxEventModel, event.event_id)
        records = session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id,
        ))
        assert persisted_event is not None and persisted_event.published_at is not None and persisted_event.retry_count == 1
        assert delivered == [event.event_id]
        assert records == 1 and record.record_id is not None
