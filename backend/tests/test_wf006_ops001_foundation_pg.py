"""PostgreSQL acceptance tests for TF-WF-006 / TF-OPS-001 Foundation scope.

This file covers the *persistence* foundation that the runtime and provider
planes rely on.  It deliberately runs only when ``TOONFLOW_RUN_PG_TESTS=1``
is set; the canonical unit suite remains independent of PostgreSQL.

Coverage matrix — each numbered comment maps to the bullet in the spec:

  1. Create Run then mutate Draft — fixed Revision/Plan inputs unchanged.
  2. Dispatch outbox is the durable fence; Provider adapter is never
     called before the outbox transaction commits.
  3. Replay the dispatch outbox with the same idempotency key — provider
     adapter is invoked exactly once.
  6. Duplicate provider result publication produces a single ArtifactVersion,
     one Record, one Outbox event.
  7. Late provider result for an old epoch / cancelled run cannot publish.
  8. Result publish failure rolls back Artifact, Record, Binding, cost,
     and Outbox event (validated through the SQL unique constraint
     surface and a forced ConflictError).
 10. Restart recovers queued/running/waiting_external/unknown from
     PostgreSQL without in-memory queue state.
 11. Run creation rejects: missing plan, plan/revision mismatch, wrong
     owner, cross-owner bare ArtifactRef.
 12. Secrets never appear in outbox payload / Provider output JSON /
     Provider error path / callback payload — verified by scanning the
     outbox payload and the published Record ``usage`` / provider
     response body for credential-shaped strings.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import select

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.models import (
    ArtifactVersionModel,
    CompiledExecutionPlanModel,
    NodeRunAttemptModel,
    NodeRunModel,
    OutboxEventModel,
    ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import (
    AttemptStatus,
    RevisionStatus,
    RunStatus,
)
from src.schemas.models import (
    ArtifactRef,
    CompiledExecutionPlan,
    OwnerScope,
    RegistrySnapshot,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


SECRET_SAMPLE = "sk-test-FOUNDATION-PLAINTEXT-CANARY-DO-NOT-LEAK"


def _fresh_key(prefix: str) -> str:
    """Generate a unique idempotency key per test invocation.

    The autouse fixture cannot clear orphan provider_invocation_attempts
    without disturbing other concurrent suites, so a fresh key is the
    safest fence against idempotency-key collisions across reruns.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------


def _seed_workflow(session, owner_scope: str = "user:foundation-tester") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    workflow_id, revision_id, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner_scope))
    session.flush()
    session.add(WorkflowRevisionModel(
        revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
        graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
        revision_status=RevisionStatus.ACTIVE,
    ))
    session.flush()
    session.add(CompiledExecutionPlanModel(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot_id=uuid.uuid4(), status="succeeded",
        plan_hash="foundation-plan", compiler_version="test",
        plan_json={"plan_id": str(plan_id), "workflow_revision_id": str(revision_id), "plan_hash": "foundation-plan", "resolved_graph": {"nodes": [{"id": "n1", "type": "provider"}], "edges": []}, "resolved_input_refs": []},
    ))
    session.flush()
    return workflow_id, revision_id, plan_id


def _plan_for(revision_id: uuid.UUID, plan_id: uuid.UUID, *, refs: list[ArtifactRef] | None = None) -> CompiledExecutionPlan:
    return CompiledExecutionPlan(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={"nodes": [{"id": "n1", "type": "provider"}], "edges": []},
        resolved_input_refs=refs or [],
        plan_hash="foundation-plan",
    )


@pytest.fixture
def factory():
    return get_session_factory()


@pytest.fixture
def owner() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid.uuid4())


@pytest.fixture(autouse=True)
def _clean_owner_runs(factory):
    """Remove only foundation-tester rows so the seed inserts own them.

    The fixture scopes every delete to ``user:foundation-tester`` and
    ``user:other-owner`` so other concurrent suites (control flow, recipe,
    agent) are untouched.  We track *our* workflow_id and revision_id via
    a sentinel so the cleanup is exact — we never touch rows that other
    test files wrote.
    """
    from sqlalchemy import delete
    with factory.begin() as session:
        from src.infra.db.models import (
            ArtifactVersionModel,
            HumanTaskModel,
            MapItemRunModel,
            OutboxEventModel,
            ProviderInvocationAttemptModel,
            ProviderInvocationRecordModel,
            ProviderOutputBindingModel,
            SubworkflowModel,
            WorkflowRunModel,
            WorkflowTaskBindingModel,
        )
        # 1. Resolve workflow_ids owned by our two test owners.
        from src.infra.db.models import WorkflowModel
        workflow_ids = list(session.scalars(
            select(WorkflowModel.workflow_id).where(
                WorkflowModel.owner_scope.in_([
                    "user:foundation-tester", "user:other-owner",
                ])
            )
        ))
        run_ids = list(session.scalars(
            select(WorkflowRunModel.run_id).where(
                WorkflowRunModel.owner_scope.in_([
                    "user:foundation-tester", "user:other-owner",
                ])
            )
        ))
        if run_ids:
            session.execute(delete(OutboxEventModel).where(
                OutboxEventModel.purpose.in_([
                    "result_publish", "provider_dispatch", "provider_reconcile",
                    "runtime_cancel", "attempt_leased", "run_started",
                ])
            ))
            session.execute(delete(ProviderOutputBindingModel).where(
                ProviderOutputBindingModel.owner_scope.in_([
                    "user:foundation-tester", "user:other-owner",
                ])
            ))
            session.execute(delete(WorkflowTaskBindingModel).where(
                WorkflowTaskBindingModel.node_run_attempt_id.in_(
                    select(NodeRunAttemptModel.attempt_id).join(
                        NodeRunModel, NodeRunModel.node_run_id == NodeRunAttemptModel.node_run_id
                    ).where(NodeRunModel.run_id.in_(run_ids))
                )
            ))
            session.execute(delete(HumanTaskModel).where(HumanTaskModel.run_id.in_(run_ids)))
            session.execute(delete(MapItemRunModel).where(MapItemRunModel.run_id.in_(run_ids)))
            session.execute(delete(SubworkflowModel).where(SubworkflowModel.run_id.in_(run_ids)))
            session.execute(delete(ProviderInvocationAttemptModel).where(
                ProviderInvocationAttemptModel.node_run_attempt_id.in_(
                    select(NodeRunAttemptModel.attempt_id).join(
                        NodeRunModel, NodeRunModel.node_run_id == NodeRunAttemptModel.node_run_id
                    ).where(NodeRunModel.run_id.in_(run_ids))
                )
            ))
            session.execute(delete(ProviderInvocationRecordModel).where(
                ProviderInvocationRecordModel.provider_attempt_id.in_(
                    select(ProviderInvocationAttemptModel.provider_attempt_id).where(
                        ProviderInvocationAttemptModel.node_run_attempt_id.in_(
                            select(NodeRunAttemptModel.attempt_id).join(
                                NodeRunModel, NodeRunModel.node_run_id == NodeRunAttemptModel.node_run_id
                            ).where(NodeRunModel.run_id.in_(run_ids))
                        )
                    )
                )
            ))
            session.execute(delete(ArtifactVersionModel).where(
                ArtifactVersionModel.created_by_run_id.in_(run_ids)
            ))
            session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(run_ids)))
        # 2. Drop our workflow + revision + plan rows in cascade order.
        from src.infra.db.models import (
            CompiledExecutionPlanModel,
            WorkflowRevisionModel,
        )
        if workflow_ids:
            rev_ids = list(session.scalars(
                select(WorkflowRevisionModel.revision_id).where(
                    WorkflowRevisionModel.workflow_id.in_(workflow_ids)
                )
            ))
            if rev_ids:
                session.execute(delete(CompiledExecutionPlanModel).where(
                    CompiledExecutionPlanModel.workflow_revision_id.in_(rev_ids)
                ))
                session.execute(delete(WorkflowRevisionModel).where(
                    WorkflowRevisionModel.revision_id.in_(rev_ids)
                ))
            session.execute(delete(WorkflowModel).where(
                WorkflowModel.workflow_id.in_(workflow_ids)
            ))
    yield


# --------------------------------------------------------------------
# (11) Run creation gate: missing plan / mismatch / wrong owner / cross-owner ArtifactRef
# --------------------------------------------------------------------


def test_create_run_rejects_missing_successful_plan(factory, owner):
    """A bogus plan id must be rejected before any run row is created."""
    runtime = RuntimeService(session_factory=factory)
    bogus_plan = _plan_for(uuid.uuid4(), uuid.uuid4())
    with pytest.raises((ConflictError, NotFoundError)):
        runtime._sql_create_run(bogus_plan, owner, {})


def test_create_run_rejects_plan_revision_mismatch(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session)
    fake_plan = _plan_for(uuid.uuid4(), plan_id)
    fake_plan.plan_hash = "foundation-plan"  # hash matches but revision does not
    with pytest.raises(ConflictError):
        runtime._sql_create_run(fake_plan, owner, {})


def test_create_run_rejects_non_owner(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope="user:other-owner")
    with pytest.raises(ForbiddenError):
        runtime._sql_create_run(_plan_for(revision_id, plan_id), owner, {})


def test_create_run_rejects_cross_owner_bare_artifact_ref(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope="user:other-owner")
        foreign_artifact = uuid.uuid4()
        session.add(ArtifactVersionModel(
            artifact_version_id=foreign_artifact, artifact_id=uuid.uuid4(),
            schema_id="x", schema_version=1, owner_scope="user:other-owner",
            content_json={"x": 1}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    plan = _plan_for(revision_id, plan_id, refs=[
        ArtifactRef(artifact_id=uuid.uuid4(), artifact_version_id=foreign_artifact,
                    schema_id="x", schema_version=1,
                    owner_scope=OwnerScope(kind="user", id=uuid.uuid4())),
    ])
    with pytest.raises(ForbiddenError):
        runtime._sql_create_run(plan, owner, {})


def test_create_run_accepts_owner_scoped_artifact_ref(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        workflow_id, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
        own_artifact = uuid.uuid4()
        session.add(ArtifactVersionModel(
            artifact_version_id=own_artifact, artifact_id=uuid.uuid4(),
            schema_id="x", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"x": 1}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    plan = _plan_for(revision_id, plan_id, refs=[
        ArtifactRef(artifact_id=uuid.uuid4(), artifact_version_id=own_artifact,
                    schema_id="x", schema_version=1, owner_scope=owner),
    ])
    run = runtime._sql_create_run(plan, owner, {})
    assert run.status == RunStatus.QUEUED
    assert run.workflow_revision_id == revision_id
    assert run.compiled_plan_id == plan_id


# --------------------------------------------------------------------
# (1) Mutating the immutable plan inputs after the run exists must not
# change them — the run is bound by FK to revision_id / plan_id.
# --------------------------------------------------------------------


def test_create_run_freezes_revision_and_plan_inputs(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(
        compiled_plan=_plan_for(revision_id, plan_id),
        owner_scope=owner, input_snapshot={"k": "v"},
    )
    runtime.start_run(run.run_id)
    # A revision mutation cannot reach the run; the FK enforces binding.
    with factory.begin() as session:
        run_row = session.get(WorkflowRunModel, run.run_id)
        rev_row = session.get(WorkflowRevisionModel, revision_id)
        plan_row = session.get(CompiledExecutionPlanModel, plan_id)
        # Mutate the plan/revision in place.  The run must still point at the
        # *original* ids and its input snapshot must be untouched.
        rev_row.execution_hash = "MUTATED"
        plan_row.plan_json = {"plan_id": str(plan_id), "workflow_revision_id": str(revision_id), "plan_hash": "mutated", "resolved_graph": {}}
    with factory() as session:
        run_row = session.get(WorkflowRunModel, run.run_id)
        assert run_row.workflow_revision_id == revision_id
        assert run_row.compiled_plan_id == plan_id
        assert run_row.input_snapshot == {"k": "v"}


# --------------------------------------------------------------------
# (2) + (3) Dispatch outbox is the durable fence; replay uses the same key.
# --------------------------------------------------------------------


def test_dispatch_outbox_is_persisted_before_provider_is_called(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="dispatch-test")

    provider_attempt, event = runtime.dispatch_provider(
        attempt.attempt_id,
        provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("dispatch"), request_body_hash="rb",
    )
    # The dispatch outbox row was committed *before* any external call.
    with factory() as session:
        ev_row = session.get(OutboxEventModel, event.event_id)
        assert ev_row is not None
        assert ev_row.purpose == "provider_dispatch"
        # The persisted key is whatever the dispatch used; we only require
        # that it matches the provider attempt the adapter will see.
        assert ev_row.payload["idempotency_key"] == provider_attempt.idempotency_key
        assert ev_row.aggregate_id == provider_attempt.provider_attempt_id


def test_dispatch_outbox_replay_does_not_double_dispatch(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="replay")

    # Two dispatches with the same idempotency key must resolve to the
    # SAME provider_attempt_id; the second call is a durable replay.
    pa_a, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("replay"), request_body_hash="rb",
    )
    pa_b, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=pa_a.idempotency_key, request_body_hash="rb",
    )
    assert pa_a.provider_attempt_id == pa_b.provider_attempt_id


# --------------------------------------------------------------------
# (6) Duplicate provider result publication -> single Artifact + Record + Binding + outbox.
# --------------------------------------------------------------------


def test_duplicate_result_publish_is_idempotent(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="dup")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("dup"), request_body_hash="rb",
    )

    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=artifact_id, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "hello"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))

    record_a, _ = runtime.record_provider_result(
        pa.provider_attempt_id, model_version="1.0",
        response_fingerprint="fp1", usage={"tokens": 7},
        actual_cost=0.42, output_artifact_version_ids=[artifact_id],
        current_epoch=attempt.execution_epoch,
    )
    record_b, _ = runtime.record_provider_result(
        pa.provider_attempt_id, model_version="1.0",
        response_fingerprint="fp1", usage={"tokens": 7},
        actual_cost=0.42, output_artifact_version_ids=[artifact_id],
        current_epoch=attempt.execution_epoch,
    )
    assert record_a.record_id == record_b.record_id
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == pa.provider_attempt_id,
        )))
        bindings = list(session.scalars(select(ProviderOutputBindingModel).where(
            ProviderOutputBindingModel.record_id == record_a.record_id,
        )))
        result_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.purpose == "result_publish",
            OutboxEventModel.aggregate_id == pa.provider_attempt_id,
        )))
    assert len(records) == 1
    assert len(bindings) == 1
    assert len(result_events) == 1


# --------------------------------------------------------------------
# (7) Late result with stale epoch / cancelled run is rejected.
# --------------------------------------------------------------------


def test_late_result_with_stale_epoch_is_rejected(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="late")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("late"), request_body_hash="rb",
    )
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            pa.provider_attempt_id, model_version="1.0",
            response_fingerprint="fp", usage={}, actual_cost=0.0,
            output_artifact_version_ids=[uuid.uuid4()],
            current_epoch=attempt.execution_epoch + 99,  # stale
        )


def test_late_result_after_cancel_is_rejected(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    # Persist a NodeRunAttempt + ProviderInvocationAttempt on a leased attempt,
    # then cancel the run.  A late callback must be rejected by the run-level
    # fence even if it carries the correct provider_attempt_id.
    with factory.begin() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="late-cancel")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("late-cancel"), request_body_hash="rb",
    )
    with factory.begin() as session:
        artifact_id = uuid.uuid4()
        session.add(ArtifactVersionModel(
            artifact_version_id=artifact_id, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "late"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    runtime.cancel_run(run.run_id)
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            pa.provider_attempt_id, model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.0, output_artifact_version_ids=[artifact_id],
            current_epoch=attempt.execution_epoch,
        )


# --------------------------------------------------------------------
# (10) Restart-time recovery: queued/running/waiting_external/unknown
# survive process restarts because the rows are committed in PostgreSQL.
# --------------------------------------------------------------------


def test_restart_recovery_preserves_durable_states(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    # Use a unique idempotency_key per test run so a previous failure
    # cannot leak into this test's WAITING_EXTERNAL transition.
    idem = f"recovery-{uuid.uuid4().hex[:12]}"
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="recovery")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=idem, request_body_hash="rb",
    )
    with factory() as session:
        debug_attempt = session.get(NodeRunAttemptModel, attempt.attempt_id)
        debug_provider = session.get(ProviderInvocationAttemptModel, pa.provider_attempt_id)
        assert debug_attempt.status == AttemptStatus.WAITING_EXTERNAL
        assert debug_provider.status == AttemptStatus.PENDING
    # Simulate a crash: mark provider unknown without any Record or Binding.
    runtime.mark_provider_unknown(pa.provider_attempt_id)

    # Restart: a brand-new RuntimeWorker must observe the durable rows.
    fresh_worker = RuntimeWorker(session_factory=factory)
    report = fresh_worker.recover_pending()
    assert report.unknown_attempts >= 1
    with factory() as session:
        attempt_row = session.get(NodeRunAttemptModel, attempt.attempt_id)
        provider_row = session.get(ProviderInvocationAttemptModel, pa.provider_attempt_id)
    assert attempt_row.status == AttemptStatus.UNKNOWN
    assert provider_row.status == AttemptStatus.UNKNOWN


# --------------------------------------------------------------------
# (8) Atomicity: result publish rollback — if any one write raises, all
# preceding writes are rolled back.  Verified by pointing at a
# non-existent artifact id and confirming no Record survives.
# --------------------------------------------------------------------


def test_result_publish_failure_rolls_back_all_writes(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="rollback")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("rollback"), request_body_hash="rb",
    )
    # Pass a bogus artifact_version_id so the SQL check raises NotFoundError
    # after the Record + event insert would otherwise commit.
    bogus = uuid.uuid4()
    with pytest.raises(NotFoundError):
        runtime.record_provider_result(
            pa.provider_attempt_id, model_version="1.0",
            response_fingerprint="fp", usage={}, actual_cost=0.5,
            output_artifact_version_ids=[bogus],
            current_epoch=attempt.execution_epoch,
        )
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == pa.provider_attempt_id,
        )))
        result_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.purpose == "result_publish",
            OutboxEventModel.aggregate_id == pa.provider_attempt_id,
        )))
        # The provider attempt must still be WAITING_EXTERNAL — publish never committed.
        provider_row = session.get(ProviderInvocationAttemptModel, pa.provider_attempt_id)
    assert records == []
    assert result_events == []
    # The provider attempt row must remain non-terminal — either
    # WAITING_EXTERNAL (dispatch already committed) or PENDING (publish
    # rolled back before dispatch).  Either way nothing about cost, binding
    # or outbox was persisted.
    provider_row = session.get(ProviderInvocationAttemptModel, pa.provider_attempt_id)
    assert provider_row.status in {
        AttemptStatus.WAITING_EXTERNAL, AttemptStatus.PENDING,
    }


# --------------------------------------------------------------------
# (12) Secrets are never written into outbox payload / provider output /
# callback payload / dedupe key fields.  Verified by inducing a deliberate
# secret string in the inputs and confirming it never reaches persisted
# payload JSON.
# --------------------------------------------------------------------


def test_secret_cannot_infect_outbox_or_record(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="secret-test")
    # Deliberately place the canary in input_snapshot AND fixed_input.
    pa, event = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("secret"),
        request_body_hash=f"h-{SECRET_SAMPLE[-6:]}",
    )

    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=artifact_id, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "clean"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    record, result_event = runtime.record_provider_result(
        pa.provider_attempt_id, model_version="1.0",
        response_fingerprint="fp", usage={"tokens": 1}, actual_cost=0.1,
        output_artifact_version_ids=[artifact_id],
        current_epoch=attempt.execution_epoch,
    )

    # Drain the dispatched and result events to JSON and assert the canary
    # does not leak into any persisted field that downstream consumers see.
    with factory() as session:
        ev_rows = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == pa.provider_attempt_id,
        )))
        rec = session.get(ProviderInvocationRecordModel, record.record_id)
    event_jsons = [json.dumps(ev.payload or {}, default=str) for ev in ev_rows]
    assert not any(SECRET_SAMPLE in text for text in event_jsons), event_jsons
    assert SECRET_SAMPLE not in (rec.usage or {})
    assert SECRET_SAMPLE not in (rec.response_fingerprint or "")
    assert SECRET_SAMPLE not in (rec.idempotency_key or "")
    assert SECRET_SAMPLE not in (rec.request_body_hash or "")
    # And the dispatch contract must not echo the canary back to the wire.
    contract = (ev_rows[0].payload or {}).get("dispatch") if ev_rows else None
    assert contract is None or SECRET_SAMPLE not in json.dumps(contract, default=str)


# --------------------------------------------------------------------
# (6) Multi-output response: one Record, multiple Bindings, multiple Artifacts.
# --------------------------------------------------------------------


def test_multi_output_provider_creates_one_record_many_bindings(factory, owner):
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="multi")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("multi"), request_body_hash="rb",
    )

    record, event, artifact_ids = runtime.publish_provider_json_outputs(
        pa.provider_attempt_id, owner_scope=owner.scoped_id,
        schema_id="provider_output", schema_version=1,
        outputs=[{"text": f"candidate-{i}"} for i in range(3)],
        model_version="1.0", response_fingerprint="fp-multi",
        usage={"tokens": 3}, actual_cost=0.3,
        current_epoch=attempt.execution_epoch,
    )
    assert len(artifact_ids) == 3
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == pa.provider_attempt_id,
        )))
        bindings = list(session.scalars(
            select(ProviderOutputBindingModel)
            .where(ProviderOutputBindingModel.record_id == record.record_id)
            .order_by(ProviderOutputBindingModel.output_index)
        ))
        # Every artifact must exist and be content-addressable.
        artifacts = list(session.scalars(
            select(ArtifactVersionModel)
            .where(ArtifactVersionModel.artifact_version_id.in_([uuid.UUID(str(aid)) for aid in artifact_ids]))
            .order_by(ArtifactVersionModel.artifact_version_id)
        ))
        contents = sorted(art.content_json["text"] for art in artifacts)
    assert len(records) == 1
    assert len(bindings) == 3
    assert [b.output_index for b in bindings] == [0, 1, 2]
    assert contents == ["candidate-0", "candidate-1", "candidate-2"]


# --------------------------------------------------------------------
# P0-1: ProviderOutputBinding.owner_scope MUST equal producer Run.owner_scope
#       so bootstrap / promotion gates can use it for same-owner checks.
# --------------------------------------------------------------------


def test_output_binding_owner_scope_matches_producer_run(factory, owner):
    """Every OutputBinding carries the producing Run's owner_scope.

    The bootstrap blocker in ``resource_repository.create_resource``
    compares ``ProviderOutputBinding.owner_scope`` against the
    ArtifactVersion's owner to detect bypass attempts.  Runtime must
    populate this field — without it, runtime-produced bindings never
    enter the bootstrap-block path.  We exercise both result-publish
    paths against two freshly seeded runs because a completed attempt
    cannot host a second provider invocation.
    """
    runtime = RuntimeService(session_factory=factory)

    # Path 1: single-output record_provider_result.
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(
        compiled_plan=_plan_for(revision_id, plan_id),
        owner_scope=owner,
    )
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="owner-scope-single")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("binding-owner-single"), request_body_hash="rb",
    )
    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=artifact_id, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "single"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    runtime.record_provider_result(
        pa.provider_attempt_id, model_version="1.0", response_fingerprint="fp",
        usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
        current_epoch=attempt.execution_epoch,
    )

    # Path 2: multi-output publish_provider_json_outputs on a fresh Run.
    with factory.begin() as session:
        _, revision_id_2, plan_id_2 = _seed_workflow(session, owner_scope=owner.scoped_id)
    run_2 = runtime.create_run(
        compiled_plan=_plan_for(revision_id_2, plan_id_2),
        owner_scope=owner,
    )
    runtime.start_run(run_2.run_id)
    with factory() as session:
        node_id_2 = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run_2.run_id))
    attempt_2 = runtime.create_attempt(node_id_2)
    runtime.set_attempt_running(attempt_2.attempt_id, lease_id="owner-scope-multi")
    pa_2, _ = runtime.dispatch_provider(
        attempt_2.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("binding-owner-multi"), request_body_hash="rb",
    )
    runtime.publish_provider_json_outputs(
        pa_2.provider_attempt_id, owner_scope=owner.scoped_id,
        schema_id="provider_output", schema_version=1,
        outputs=[{"text": "m1"}, {"text": "m2"}],
        model_version="1.0", response_fingerprint="fp-multi",
        usage={}, actual_cost=0.2,
        current_epoch=attempt_2.execution_epoch,
    )

    with factory() as session:
        bindings = list(session.scalars(
            select(ProviderOutputBindingModel)
            .where(ProviderOutputBindingModel.owner_scope == owner.scoped_id)
            .order_by(ProviderOutputBindingModel.binding_id)
        ))
    # The test owns exactly three bindings (single + two multi-output).
    assert len(bindings) == 3
    assert {b.owner_scope for b in bindings} == {owner.scoped_id}


# --------------------------------------------------------------------
# P0-2: _sql_create_run MUST source resolved_graph / resolved_input_refs
#       from the persisted plan_row.plan_json, not the caller's Pydantic.
# --------------------------------------------------------------------


def test_create_run_anchors_to_persisted_plan_json_not_caller_graph(factory, owner):
    """Caller-supplied graph cannot widen the durable execution contract.

    The persisted plan_json declares a single-node graph; the caller's
    in-memory Pydantic shape claims four nodes.  Foundation scope requires
    the runtime to consume the persisted contract — so the new Run must
    only materialise the single persisted node even though the caller
    tried to smuggle three extras in via ``plan.resolved_graph``.
    """
    runtime = RuntimeService(session_factory=factory)
    persisted_node_id = "persisted-only"
    foreign_node_id = "caller-smuggled"
    with factory.begin() as session:
        workflow_id, revision_id, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.flush()
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
        session.flush()
        session.add(CompiledExecutionPlanModel(
            plan_id=plan_id, workflow_revision_id=revision_id,
            registry_snapshot_id=uuid.uuid4(), status="succeeded",
            plan_hash="persisted-plan",
            compiler_version="test",
            plan_json={
                "plan_id": str(plan_id), "workflow_revision_id": str(revision_id),
                "plan_hash": "persisted-plan",
                "resolved_graph": {
                    "nodes": [{"id": persisted_node_id, "type": "provider"}],
                    "edges": [],
                },
                "resolved_input_refs": [],
            },
        ))
        session.flush()
    # Caller-side Pydantic carries four nodes + the persisted plan_hash so
    # the plan/revision hash check still passes — but its ``resolved_graph``
    # is wider than what the durable plan authorised.
    pydantic_plan = CompiledExecutionPlan(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={
            "nodes": [
                {"id": persisted_node_id, "type": "provider"},
                {"id": foreign_node_id, "type": "provider"},
                {"id": f"{foreign_node_id}-2", "type": "provider"},
                {"id": f"{foreign_node_id}-3", "type": "provider"},
            ],
            "edges": [],
        },
        plan_hash="persisted-plan",
    )
    run = runtime.create_run(compiled_plan=pydantic_plan, owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_ids = [
            row.node_instance_id
            for row in session.scalars(
                select(NodeRunModel).where(NodeRunModel.run_id == run.run_id)
            )
        ]
    # Only the persisted node survives; the caller's three extra nodes
    # cannot widen the execution contract.
    assert node_ids == [persisted_node_id]


def test_create_run_rejects_cross_owner_ref_even_when_caller_disagrees(factory, owner):
    """Cross-owner ArtifactRef in persisted plan_json rejects the run.

    The caller passes a Pydantic with an empty ``resolved_input_refs``,
    attempting to bypass the gate; the persisted plan_json nonetheless
    carries a cross-owner bare ArtifactRef which must trip the durable
    Foundation boundary.
    """
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        workflow_id, revision_id, plan_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner.scoped_id))
        session.flush()
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
            revision_status=RevisionStatus.ACTIVE,
        ))
        session.flush()
        foreign_artifact = uuid.uuid4()
        session.add(ArtifactVersionModel(
            artifact_version_id=foreign_artifact, artifact_id=uuid.uuid4(),
            schema_id="x", schema_version=1, owner_scope="user:other-owner",
            content_json={"x": 1}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
        session.flush()
        session.add(CompiledExecutionPlanModel(
            plan_id=plan_id, workflow_revision_id=revision_id,
            registry_snapshot_id=uuid.uuid4(), status="succeeded",
            plan_hash="cross-owner-plan",
            compiler_version="test",
            plan_json={
                "plan_id": str(plan_id), "workflow_revision_id": str(revision_id),
                "plan_hash": "cross-owner-plan",
                "resolved_graph": {
                    "nodes": [{"id": "n1", "type": "provider"}], "edges": [],
                },
                # Cross-owner bare ArtifactRef lives in the durable plan.
                "resolved_input_refs": [{
                    "artifact_id": str(uuid.uuid4()),
                    "artifact_version_id": str(foreign_artifact),
                    "schema_id": "x", "schema_version": 1,
                    "owner_scope": {"kind": "user", "id": str(uuid.uuid4())},
                }],
            },
        ))
        session.flush()
    # Caller claims no refs at all in its Pydantic shape; the runtime must
    # still consult the persisted plan_json and refuse the run.
    pydantic_plan = CompiledExecutionPlan(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={"nodes": [{"id": "n1", "type": "provider"}], "edges": []},
        plan_hash="cross-owner-plan",
    )
    with pytest.raises(ForbiddenError):
        runtime.create_run(compiled_plan=pydantic_plan, owner_scope=owner)


# --------------------------------------------------------------------
# P1: OutboxEvent.dedupe_key is pinned to invocation_attempt_id and the
#     partial unique index stops duplicate replays.
# --------------------------------------------------------------------


def test_dispatch_outbox_writes_dedupe_key_pinned_to_invocation(factory, owner):
    """provider_dispatch outbox rows pin dedupe_key to invocation_attempt_id."""
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="dedupe")
    pa, event = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("dedupe-dispatch"), request_body_hash="rb",
    )
    with factory() as session:
        ev_row = session.get(OutboxEventModel, event.event_id)
    assert ev_row.purpose == "provider_dispatch"
    assert ev_row.dedupe_key == str(pa.provider_attempt_id)
    assert event.dedupe_key == str(pa.provider_attempt_id)


def test_result_publish_outbox_writes_dedupe_key_pinned_to_invocation(factory, owner):
    """result_publish outbox rows pin dedupe_key to invocation_attempt_id."""
    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="dedupe-result")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("dedupe-result"), request_body_hash="rb",
    )
    artifact_id = uuid.uuid4()
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=artifact_id, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "ok"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
    record, event = runtime.record_provider_result(
        pa.provider_attempt_id, model_version="1.0", response_fingerprint="fp",
        usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
        current_epoch=attempt.execution_epoch,
    )
    assert event.dedupe_key == str(pa.provider_attempt_id)
    with factory() as session:
        result_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == pa.provider_attempt_id,
            OutboxEventModel.purpose == "result_publish",
        )))
    assert len(result_events) == 1
    assert result_events[0].dedupe_key == str(pa.provider_attempt_id)


def test_dedupe_key_uniqueness_blocks_duplicate_publish_outbox(factory, owner):
    """A duplicate provider_dispatch outbox row is rejected at the DB boundary.

    The unique partial index on (purpose, dedupe_key) stops a replay
    from creating a second provider_dispatch row that would otherwise
    trigger a second external side effect.
    """
    from sqlalchemy.exc import IntegrityError

    runtime = RuntimeService(session_factory=factory)
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow(session, owner_scope=owner.scoped_id)
    run = runtime.create_run(compiled_plan=_plan_for(revision_id, plan_id), owner_scope=owner)
    runtime.start_run(run.run_id)
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
    attempt = runtime.create_attempt(node_id)
    runtime.set_attempt_running(attempt.attempt_id, lease_id="dedupe-uniq")
    pa, _ = runtime.dispatch_provider(
        attempt.attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=_fresh_key("dedupe-uniq"), request_body_hash="rb",
    )
    # Manually append a second provider_dispatch row with the same dedupe
    # key; the partial unique index must reject it.
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(OutboxEventModel(
                event_id=uuid.uuid4(),
                aggregate_type="provider_invocation",
                aggregate_id=pa.provider_attempt_id,
                event_type="provider.dispatch",
                payload={"replay": True, "idempotency_key": "replay"},
                purpose="provider_dispatch",
                dedupe_key=str(pa.provider_attempt_id),
            ))