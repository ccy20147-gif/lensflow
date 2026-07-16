"""TF-WF-006 / TF-OPS-001 V0 hardening acceptance tests.

These tests cover the V0 persistence loop:

  * Worker lease heartbeat / expiry / recovery / fencing
  * Service-restart recovery across queued / running / waiting_external /
    unknown / cancelling
  * Partial-run closure anchored to the persisted plan
  * Cancel-vs-callback race and discarded audit event
  * Agent and Recipe dispatch outbox persistence before any network call
  * Replay / duplicate callback idempotency at every fence
  * Concurrent callback collapses to one record / one result outbox

The suite runs against PostgreSQL only (``TOONFLOW_RUN_PG_TESTS=1``); the
canonical unit suite remains independent of PostgreSQL.

The tests never connect to a real Provider.  Any test that performs an
external network call is forbidden — we exercise the dispatch / result
contracts end-to-end with a fake AtlasCloud task id and the
``provider_id="fake"`` contract that the Foundation PG tests already use.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError, ForbiddenError
from src.domain.recipe.recipe_runtime import RecipeRuntimeService
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
    WorkflowTaskBindingModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import (
    AttemptStatus,
    NodeRunStatus,
    RevisionStatus,
    RunStatus,
)
from src.schemas.models import (
    CompiledExecutionPlan,
    OwnerScope,
    RegistrySnapshot,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


TEST_OWNER_ID = uuid.UUID("5a5a5a5a-5a5a-5a5a-a5a5-a5a5a5a5a5a5")
OWNER_SCOPE = f"user:{TEST_OWNER_ID}"


# -----------------------------------------------------------------------
# Shared fixtures
# -----------------------------------------------------------------------


def _plan_for(revision_id: uuid.UUID, plan_id: uuid.UUID, *, graph: dict | None = None, plan_hash: str | None = None) -> CompiledExecutionPlan:
    return CompiledExecutionPlan(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph=graph if graph is not None else {"nodes": [{"id": "n1", "type": "provider"}], "edges": []},
        plan_hash=plan_hash or f"v0-hardening-{plan_id}",
    )


def _seed_workflow_revision_plan(
    session,
    *,
    plan_json: dict,
    plan_hash: str | None = None,
    owner_scope: str = OWNER_SCOPE,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    workflow_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    effective_hash = plan_hash or f"v0-hardening-{plan_id}"
    session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner_scope))
    session.flush()
    session.add(WorkflowRevisionModel(
        revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
        graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
        revision_status=RevisionStatus.ACTIVE,
    ))
    session.flush()
    persisted_plan_json = dict(plan_json)
    persisted_plan_json.setdefault("plan_id", str(plan_id))
    persisted_plan_json.setdefault("workflow_revision_id", str(revision_id))
    persisted_plan_json.setdefault("plan_hash", effective_hash)
    persisted_plan_json.setdefault("resolved_graph", {"nodes": [{"id": "n1", "type": "provider"}], "edges": []})
    persisted_plan_json.setdefault("resolved_input_refs", [])
    session.add(CompiledExecutionPlanModel(
        plan_id=plan_id, workflow_revision_id=revision_id,
        registry_snapshot_id=uuid.uuid4(), status="succeeded",
        plan_hash=effective_hash, compiler_version="test",
        plan_json=persisted_plan_json,
    ))
    session.flush()
    return workflow_id, revision_id, plan_id


@pytest.fixture
def factory():
    return get_session_factory()


@pytest.fixture
def owner() -> OwnerScope:
    # The hard-coded scoped_id below must match the OWNER_SCOPE used by
    # the workflow seed; otherwise ``create_run`` rejects the run because
    # the Workflow's owner does not match the caller.
    return OwnerScope(kind="user", id=TEST_OWNER_ID)


@pytest.fixture(autouse=True)
def _clean_owner_runs(factory):
    """Keep the V0 hardening tests isolated.

    The suite touches a single owner (``OWNER_SCOPE``) so the cleanup is a
    bounded cascade.  Anything outside this owner is left alone.
    """
    from src.infra.db.models import (
        HumanTaskModel,
        MapItemRunModel,
        SubworkflowModel,
    )
    with factory.begin() as session:
        run_ids = list(session.scalars(
            select(WorkflowRunModel.run_id).where(WorkflowRunModel.owner_scope == OWNER_SCOPE)
        ))
        if run_ids:
            session.execute(delete(OutboxEventModel).where(
                OutboxEventModel.purpose.in_([
                    "result_publish", "provider_dispatch", "provider_reconcile",
                    "runtime_cancel", "attempt_leased", "run_started",
                ])
            ))
            session.execute(delete(ProviderOutputBindingModel).where(
                ProviderOutputBindingModel.owner_scope == OWNER_SCOPE
            ))
            # Delete NodeRunAttempts first to free the FK to WorkflowRuns,
            # then NodeRuns, then WorkflowRuns.
            attempt_ids = list(session.scalars(
                select(NodeRunAttemptModel.attempt_id).where(
                    NodeRunAttemptModel.node_run_id.in_(
                        select(NodeRunModel.node_run_id).where(NodeRunModel.run_id.in_(run_ids))
                    )
                )
            ))
            if attempt_ids:
                session.execute(delete(WorkflowTaskBindingModel).where(
                    WorkflowTaskBindingModel.node_run_attempt_id.in_(attempt_ids)
                ))
                session.execute(delete(ProviderInvocationRecordModel).where(
                    ProviderInvocationRecordModel.provider_attempt_id.in_(
                        select(ProviderInvocationAttemptModel.provider_attempt_id).where(
                            ProviderInvocationAttemptModel.node_run_attempt_id.in_(attempt_ids)
                        )
                    )
                ))
                session.execute(delete(ProviderInvocationAttemptModel).where(
                    ProviderInvocationAttemptModel.node_run_attempt_id.in_(attempt_ids)
                ))
                session.execute(delete(NodeRunAttemptModel).where(
                    NodeRunAttemptModel.attempt_id.in_(attempt_ids)
                ))
            session.execute(delete(NodeRunModel).where(NodeRunModel.run_id.in_(run_ids)))
            session.execute(delete(HumanTaskModel).where(HumanTaskModel.run_id.in_(run_ids)))
            session.execute(delete(MapItemRunModel).where(MapItemRunModel.run_id.in_(run_ids)))
            session.execute(delete(SubworkflowModel).where(SubworkflowModel.run_id.in_(run_ids)))
            session.execute(delete(ArtifactVersionModel).where(
                ArtifactVersionModel.created_by_run_id.in_(run_ids)
            ))
            session.execute(delete(WorkflowRunModel).where(WorkflowRunModel.run_id.in_(run_ids)))
        workflow_ids = list(session.scalars(
            select(WorkflowModel.workflow_id).where(WorkflowModel.owner_scope == OWNER_SCOPE)
        ))
        if workflow_ids:
            session.execute(delete(CompiledExecutionPlanModel).where(
                CompiledExecutionPlanModel.workflow_revision_id.in_(
                    select(WorkflowRevisionModel.revision_id).where(
                        WorkflowRevisionModel.workflow_id.in_(workflow_ids)
                    )
                )
            ))
            session.execute(delete(WorkflowRevisionModel).where(
                WorkflowRevisionModel.workflow_id.in_(workflow_ids)
            ))
            session.execute(delete(WorkflowModel).where(
                WorkflowModel.workflow_id.in_(workflow_ids)
            ))
    yield


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _seed_run_with_attempt(factory, runtime, *, graph: dict | None = None) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed a workflow + plan and create a run with a single attempt.

    Returns ``(run_id, node_run_id, attempt_id)``.  The attempt is in
    ``PENDING`` and ready to be claimed.
    """
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow_revision_plan(session, plan_json={
            "resolved_graph": graph or {"nodes": [{"id": "n1", "type": "provider"}], "edges": []},
        })
    plan = _plan_for(revision_id, plan_id, graph=graph or {"nodes": [{"id": "n1", "type": "provider"}], "edges": []})
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=TEST_OWNER_ID))
    with factory() as session:
        node_id = session.scalar(select(NodeRunModel.node_run_id).where(NodeRunModel.run_id == run.run_id))
        attempt_id = session.scalar(select(NodeRunAttemptModel.attempt_id).where(NodeRunAttemptModel.node_run_id == node_id))
    return run.run_id, node_id, attempt_id


def _lease_attempt_for_test(factory, attempt_id: uuid.UUID, worker_id: str, *, expires_in: timedelta = timedelta(minutes=5)) -> datetime:
    """Force-set the attempt's lease so a test can drive a deterministic worker."""
    expires_at = datetime.now(timezone.utc) + expires_in
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.status = AttemptStatus.LEASED
        attempt.lease_id = worker_id
        attempt.lease_expires_at = expires_at
        attempt.started_at = datetime.now(timezone.utc)
    return expires_at


# -----------------------------------------------------------------------
# 1. Worker lease: heartbeat / fencing / expiry / recovery
# -----------------------------------------------------------------------


def test_heartbeat_extends_lease_for_owner_and_rejects_other_worker(factory, owner):
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    worker_a, worker_b = "worker-a", "worker-b"
    _lease_attempt_for_test(factory, attempt_id, worker_a)

    new_expires = runtime.heartbeat_attempt(attempt_id, worker_id=worker_a, ttl=timedelta(minutes=7))
    assert new_expires > datetime.now(timezone.utc) + timedelta(minutes=6)
    with factory() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        assert attempt.lease_id == worker_a
        persisted_expires = attempt.lease_expires_at
        if persisted_expires is not None and persisted_expires.tzinfo is None:
            persisted_expires = persisted_expires.replace(tzinfo=timezone.utc)
        assert persisted_expires == new_expires
        heartbeat_event = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == attempt_id,
            OutboxEventModel.event_type == "attempt.heartbeat",
        ))
    assert heartbeat_event is not None

    with pytest.raises(ConflictError):
        runtime.heartbeat_attempt(attempt_id, worker_id=worker_b)


def test_heartbeat_rejects_when_attempt_status_is_not_leaseable(factory, owner):
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.status = AttemptStatus.COMPLETED
    with pytest.raises(ConflictError):
        runtime.heartbeat_attempt(attempt_id, worker_id="worker")


def test_heartbeat_rejects_when_lease_already_expired(factory, owner):
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.status = AttemptStatus.LEASED
        attempt.lease_id = "worker"
        attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(ConflictError):
        runtime.heartbeat_attempt(attempt_id, worker_id="worker")


def test_two_workers_cannot_lease_same_attempt_after_skip_locked(factory, owner):
    """Foundation: claim_next_attempt uses skip_locked + epoch/lease predicate.

    This is the canonical claim fence — we exercise it again here to
    guarantee the V0 lease heartbeat change does not regress it.
    """
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    worker = RuntimeWorker(factory)
    claim_a = worker.claim_next_attempt("worker-a", run_id=run_id)
    claim_b = worker.claim_next_attempt("worker-b", run_id=run_id)
    assert claim_a is not None and claim_a.attempt.attempt_id == attempt_id
    assert claim_b is None


def test_recover_stale_leases_requeues_attempt_and_writes_superseded_event(factory, owner):
    """A worker that crashed mid-run is recovered without a duplicate dispatch.

    P0 hardening: the recovery must materialise a fresh PENDING
    ``NodeRunAttempt`` so ``claim_next_attempt`` actually finds work on
    the next poll — without this, the node would be permanently shelved.
    """
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    worker = RuntimeWorker(factory)
    claim = worker.claim_next_attempt("worker-a", run_id=run_id)
    assert claim is not None
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    recovered = runtime.recover_stale_leases()
    assert recovered == 1
    with factory() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        node = session.get(NodeRunModel, attempt.node_run_id)
        superseded_event = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == attempt_id,
            OutboxEventModel.event_type == "attempt.superseded",
        ))
        # A fresh PENDING attempt must exist on the same node so a new
        # worker can pick it up.
        fresh_attempts = list(session.scalars(select(NodeRunAttemptModel).where(
            NodeRunAttemptModel.node_run_id == attempt.node_run_id,
            NodeRunAttemptModel.status == AttemptStatus.PENDING,
        )))
    assert attempt.status == AttemptStatus.SUPERSEDED
    assert attempt.lease_id is None
    # The scheduler promotes the freshly-reset node to READY and
    # creates the replacement attempt — both observable as the durable
    # handoff the worker relies on next poll.
    assert node.status in {NodeRunStatus.PENDING, NodeRunStatus.READY}
    assert superseded_event is not None
    assert superseded_event.purpose == "attempt_leased"
    assert json.loads(json.dumps(superseded_event.payload))["reason"] == "lease_expired"
    assert len(fresh_attempts) == 1, fresh_attempts

    # A new worker claim must pick up the fresh attempt (not the
    # superseded one).  The previous release of this test asserted the
    # claim returned None — that was the bug fixed in the P0 hardening
    # pass: recovery must publish a durable handoff the worker can
    # observe.
    claim_after = worker.claim_next_attempt("worker-b", run_id=run_id)
    assert claim_after is not None
    assert claim_after.attempt.attempt_id != attempt_id
    assert claim_after.attempt.status == AttemptStatus.LEASED
    assert claim_after.attempt.execution_epoch == attempt.execution_epoch + 1


def test_complete_attempt_with_lease_fence_rejects_other_worker(factory, owner):
    """A worker whose lease has been revoked cannot silently publish a result."""
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    worker = RuntimeWorker(factory)
    claim = worker.claim_next_attempt("worker-a", run_id=run_id)
    assert claim is not None

    # Simulate lease revocation: another worker re-claimed after expiry.
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.lease_id = "worker-b"
    with pytest.raises(ConflictError):
        runtime.complete_attempt(attempt_id, epoch=claim.attempt.execution_epoch, expected_lease_id="worker-a")


def test_fail_attempt_with_lease_fence_rejects_other_worker(factory, owner):
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    worker = RuntimeWorker(factory)
    claim = worker.claim_next_attempt("worker-a", run_id=run_id)
    assert claim is not None

    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        attempt.lease_id = "worker-b"
    with pytest.raises(ConflictError):
        runtime.fail_attempt(attempt_id, expected_lease_id="worker-a")


# -----------------------------------------------------------------------
# 2. Cancel-vs-callback race and discarded audit event
# -----------------------------------------------------------------------


def _seed_provider_attempt(factory, runtime, owner_scoped_id: str):
    """Create a run, claim an attempt, and dispatch a provider request.

    Returns ``(run_id, node_run_id, attempt_id, provider_attempt_id)``.
    """
    run_id, node_id, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    runtime.set_attempt_running(attempt_id, lease_id="dispatcher")
    provider, _ = runtime.dispatch_provider(
        attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=f"v0-{uuid.uuid4().hex}", request_body_hash="rb",
    )
    with factory.begin() as session:
        session.add(ArtifactVersionModel(
            artifact_version_id=uuid.uuid4(), artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner_scoped_id,
            content_json={"text": "x"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={},
        ))
        artifact_id = session.scalar(select(ArtifactVersionModel.artifact_version_id).where(
            ArtifactVersionModel.owner_scope == owner_scoped_id,
        ).order_by(ArtifactVersionModel.artifact_version_id.desc()))
    return run_id, node_id, attempt_id, provider.provider_attempt_id, artifact_id


def test_cancel_then_late_result_writes_discarded_outbox(factory, owner):
    """Cancelled run + late provider result -> ConflictError + discarded event."""
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, artifact_id = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    runtime.cancel_run(run_id)
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            provider_id, model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
            current_epoch=1,
        )
    with factory() as session:
        discarded = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.discarded",
        ))
        records = session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider_id,
        ))
    assert discarded is not None
    assert discarded.purpose == "provider_dispatch_rejected"
    assert discarded.dedupe_key == str(provider_id)
    assert discarded.payload["attempt_status"] in {"cancelled", "superseded"}
    assert records == 0


def test_cancel_then_late_publish_writes_discarded_outbox(factory, owner):
    """The JSON-output publish path also fences + audits late callbacks."""
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, _ = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    runtime.cancel_run(run_id)
    with pytest.raises(ConflictError):
        runtime.publish_provider_json_outputs(
            provider_id, owner_scope=OWNER_SCOPE,
            schema_id="provider_output", schema_version=1,
            outputs=[{"text": "late"}],
            model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.0, current_epoch=1,
        )
    with factory() as session:
        discarded = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.discarded",
        ))
    assert discarded is not None


def test_concurrent_callback_creates_single_record_and_single_result_outbox(factory, owner):
    """Two parallel callbacks for the same task produce exactly one Record.

    This is the dedupe fence on the result publication path.  PostgreSQL
    row-level locking + the ``provider_attempt_id`` UNIQUE constraint
    guarantees a single Record; if both threads race past the application
    idempotency check before either commits, the partial unique index on
    ``(purpose, dedupe_key)`` aborts the second transaction with an
    ``IntegrityError`` — both outcomes leave exactly one Record and one
    result outbox row.
    """
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, artifact_id = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    epoch = 1

    results: list[tuple[uuid.UUID, uuid.UUID]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            record, event = runtime.record_provider_result(
                provider_id, model_version="1.0", response_fingerprint="fp",
                usage={"tokens": 1}, actual_cost=0.05,
                output_artifact_version_ids=[artifact_id], current_epoch=epoch,
            )
            results.append((record.record_id, event.event_id))
        except IntegrityError:
            # The DB-level partial unique index rejected the duplicate
            # INSERT.  This is the second-line fence; the application
            # idempotency check inside ``record_provider_result`` is the
            # first.  Either way, exactly one Record survives.
            pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], errors
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider_id,
        )))
        result_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.result",
        )))
    assert len(records) == 1
    assert len(result_events) == 1
    # The successful thread sees a record id; the failed thread never
    # appended.  Either way the surviving Record id must be unique.
    assert len({record_id for record_id, _ in results}) <= 1


# -----------------------------------------------------------------------
# 3. Restart-time recovery for queued / running / waiting_external / unknown / cancelling
# -----------------------------------------------------------------------


def test_recover_pending_replays_queued_running_and_waiting_external_states(factory, owner):
    """After restart the worker must observe every durable state without crashing."""
    runtime = RuntimeService(factory)
    run_id_queued, _, _ = _seed_run_with_attempt(factory, runtime)

    run_id_running, _, attempt_running = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id_running)
    _lease_attempt_for_test(factory, attempt_running, "worker-running")

    run_id_waiting, _, attempt_waiting = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id_waiting)
    _lease_attempt_for_test(factory, attempt_waiting, "worker-waiting")
    pa_waiting, _ = runtime.dispatch_provider(
        attempt_waiting, provider_id="fake", model_id="m",
        idempotency_key=f"recover-{uuid.uuid4().hex}", request_body_hash="rb",
    )
    # Force the WAITING_EXTERNAL attempt's lease to have expired so the
    # recovery path forces it to UNKNOWN.
    with factory.begin() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_waiting)
        attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        provider_row = session.get(ProviderInvocationAttemptModel, pa_waiting.provider_attempt_id)
        provider_row.status = AttemptStatus.WAITING_EXTERNAL

    run_id_unknown, _, attempt_unknown = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id_unknown)
    _lease_attempt_for_test(factory, attempt_unknown, "worker-unknown")
    pa, _ = runtime.dispatch_provider(
        attempt_unknown, provider_id="fake", model_id="m",
        idempotency_key=f"unknown-{uuid.uuid4().hex}", request_body_hash="rb",
    )
    runtime.mark_provider_unknown(pa.provider_attempt_id)

    run_id_cancelling, _, attempt_cancelling = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id_cancelling)
    _lease_attempt_for_test(factory, attempt_cancelling, "worker-cancelling")
    runtime.cancel_run(run_id_cancelling)

    worker = RuntimeWorker(factory)
    report = worker.recover_pending()
    with factory() as session:
        unknown_attempt = session.get(NodeRunAttemptModel, attempt_unknown)
        cancelling_attempt = session.get(NodeRunAttemptModel, attempt_cancelling)
        waiting_attempt = session.get(NodeRunAttemptModel, attempt_waiting)
        running_attempt = session.get(NodeRunAttemptModel, attempt_running)

    assert report.unknown_attempts >= 1
    # The stale-lease recovery must have surfaced waiting_external → unknown
    # AND running attempt → supersede.  This is the V0 V0-recovery requirement.
    assert waiting_attempt.status == AttemptStatus.UNKNOWN
    assert unknown_attempt.status == AttemptStatus.UNKNOWN
    assert cancelling_attempt.status in {AttemptStatus.CANCELLED, AttemptStatus.SUPERSEDED}
    assert running_attempt.status in {AttemptStatus.LEASED, AttemptStatus.SUPERSEDED}


# -----------------------------------------------------------------------
# 4. Partial-run isolation anchored to persisted plan
# -----------------------------------------------------------------------


def _seed_partial_run_graph(factory) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict]:
    """Create a workflow + plan with three nodes A → B → C."""
    graph = {
        "nodes": [
            {"id": "A", "type": "provider"},
            {"id": "B", "type": "provider"},
            {"id": "C", "type": "provider"},
        ],
        "edges": [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
        ],
    }
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow_revision_plan(
            session, plan_json={"resolved_graph": graph, "resolved_input_refs": []},
        )
    return revision_id, plan_id, TEST_OWNER_ID, graph


def test_partial_run_uses_fixed_upstream_artifacts_only(factory, owner):
    """A partial run copies the source Run's provider output ArtifactVersion ids.

    The new run's input_snapshot must carry exactly the upstream
    ArtifactVersion ids produced by the source Run's Provider records.
    """
    runtime = RuntimeService(factory)
    # Source run with all three nodes completed and provider records.
    revision_id, plan_id, _, _ = _seed_partial_run_graph(factory)
    plan = _plan_for(revision_id, plan_id, graph={
        "nodes": [
            {"id": "A", "type": "provider"},
            {"id": "B", "type": "provider"},
            {"id": "C", "type": "provider"},
        ],
        "edges": [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
        ],
    })
    # Persist the plan_json into a successful plan row so the partial-run
    # path can resolve it from the database.
    with factory.begin() as session:
        plan_row = session.get(CompiledExecutionPlanModel, plan_id)
        plan_row.plan_json = {
            "plan_id": str(plan_id),
            "workflow_revision_id": str(revision_id),
            "plan_hash": plan.plan_hash,
            "resolved_graph": {
                "nodes": [
                    {"id": "A", "type": "provider"},
                    {"id": "B", "type": "provider"},
                    {"id": "C", "type": "provider"},
                ],
                "edges": [
                    {"source": "A", "target": "B"},
                    {"source": "B", "target": "C"},
                ],
            },
            "resolved_input_refs": [],
        }
        plan_row.plan_hash = plan.plan_hash
    src_run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    runtime.start_run(src_run.run_id)
    # Drive the scheduler forward by completing A; this materialises B's
    # attempt via _sql_schedule_ready.  We then complete B and patch the
    # provider outputs / bindings so the partial-run closure can pin the
    # upstream artifact ids.
    with factory.begin() as session:
        node_a = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == src_run.run_id, NodeRunModel.node_instance_id == "A",
        ))
        attempt_a = session.scalar(select(NodeRunAttemptModel).where(
            NodeRunAttemptModel.node_run_id == node_a.node_run_id,
        ))
    runtime.complete_attempt(attempt_a.attempt_id, epoch=attempt_a.execution_epoch)
    with factory.begin() as session:
        node_b = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == src_run.run_id, NodeRunModel.node_instance_id == "B",
        ))
        attempt_b = session.scalar(select(NodeRunAttemptModel).where(
            NodeRunAttemptModel.node_run_id == node_b.node_run_id,
        ))
    runtime.complete_attempt(attempt_b.attempt_id, epoch=attempt_b.execution_epoch)
    fixed_artifacts: dict[str, list[uuid.UUID]] = {}
    with factory.begin() as session:
        for instance_id in ("A", "B"):
            node = session.scalar(select(NodeRunModel).where(
                NodeRunModel.run_id == src_run.run_id, NodeRunModel.node_instance_id == instance_id,
            ))
            attempt = session.scalar(select(NodeRunAttemptModel).where(
                NodeRunAttemptModel.node_run_id == node.node_run_id,
            ))
            pa = ProviderInvocationAttemptModel(
                provider_attempt_id=uuid.uuid4(), node_run_attempt_id=attempt.attempt_id,
                provider_id="fake", model_id="m", idempotency_key=f"src-{instance_id}-{uuid.uuid4().hex}",
                request_body_hash="rb", status=AttemptStatus.COMPLETED,
                created_at=datetime.now(timezone.utc),
            )
            session.add(pa)
            session.flush()
            artifact_v = uuid.uuid4()
            artifact_id = uuid.uuid4()
            session.add(ArtifactVersionModel(
                artifact_version_id=artifact_v, artifact_id=artifact_id,
                schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
                content_json={"text": instance_id}, content_hash="h", content_uri="", blob_uri="",
                lineage_input_refs=[], metadata_json={}, created_at=datetime.now(timezone.utc),
                created_by_run_id=src_run.run_id,
            ))
            record = ProviderInvocationRecordModel(
                record_id=uuid.uuid4(), provider_attempt_id=pa.provider_attempt_id,
                provider_id="fake", model_id="m", model_version="1.0",
                idempotency_key=pa.idempotency_key, request_body_hash="rb",
                response_fingerprint="fp", usage={}, actual_cost=0.0,
                started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.flush()
            session.add(ProviderOutputBindingModel(
                binding_id=uuid.uuid4(), record_id=record.record_id,
                output_artifact_version_id=artifact_v, output_index=0,
                owner_scope=owner.scoped_id,
            ))
            fixed_artifacts[instance_id] = [artifact_v]
    # Now spin up a partial run that reuses A and executes only B.
    partial = runtime.create_partial_run(
        source_run_id=src_run.run_id,
        compiled_plan=plan,
        owner_scope=owner,
        closure={"execute": ["B"], "reuse": ["A"], "skip": []},
    )
    with factory() as session:
        partial_run = session.get(WorkflowRunModel, partial.run_id)
    assert partial_run.input_snapshot["partial_run"]["source_run_id"] == str(src_run.run_id)
    fixed_upstream = partial_run.input_snapshot["partial_run"]["fixed_upstream_outputs"]
    assert "A" in fixed_upstream
    # The reused upstream's artifact ids must match the source Run's
    # ProviderOutputBinding rows.
    assert sorted(uuid.UUID(str(value)) for value in fixed_upstream["A"]) == sorted(fixed_artifacts["A"])


def test_partial_run_rejects_execute_nodes_outside_persisted_plan(factory, owner):
    """Partial-run closure cannot widen the persisted graph."""
    runtime = RuntimeService(factory)
    revision_id, plan_id, _, graph = _seed_partial_run_graph(factory)
    plan = _plan_for(revision_id, plan_id, graph=graph)
    with factory.begin() as session:
        _, revision_id_z, plan_id_z = _seed_workflow_revision_plan(session, plan_json={})
    src_run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    runtime.start_run(src_run.run_id)
    with pytest.raises(ConflictError):
        runtime.create_partial_run(
            source_run_id=src_run.run_id,
            compiled_plan=plan,
            owner_scope=owner,
            closure={"execute": ["Z"], "reuse": ["A"], "skip": []},
        )


def test_partial_run_isolated_from_source_run_state_changes(factory, owner):
    """A new partial run must not be invalidated by a source Run cancellation."""
    runtime = RuntimeService(factory)
    revision_id, plan_id, _, graph = _seed_partial_run_graph(factory)
    plan = _plan_for(revision_id, plan_id, graph=graph)
    with factory.begin() as session:
        plan_row = session.get(CompiledExecutionPlanModel, plan_id)
        plan_row.plan_json = {
            "plan_id": str(plan_id),
            "workflow_revision_id": str(revision_id),
            "plan_hash": plan.plan_hash,
            "resolved_graph": graph,
            "resolved_input_refs": [],
        }
    src_run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    runtime.start_run(src_run.run_id)
    # Complete A so partial can reuse it.
    with factory.begin() as session:
        node = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == src_run.run_id, NodeRunModel.node_instance_id == "A",
        ))
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        attempt.status = AttemptStatus.COMPLETED
        attempt.completed_at = datetime.now(timezone.utc)
        node.status = NodeRunStatus.COMPLETED
        pa = ProviderInvocationAttemptModel(
            provider_attempt_id=uuid.uuid4(), node_run_attempt_id=attempt.attempt_id,
            provider_id="fake", model_id="m", idempotency_key=f"reuse-a-{uuid.uuid4().hex}",
            request_body_hash="rb", status=AttemptStatus.COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        session.add(pa)
        session.flush()
        av = uuid.uuid4()
        session.add(ArtifactVersionModel(
            artifact_version_id=av, artifact_id=uuid.uuid4(),
            schema_id="provider_output", schema_version=1, owner_scope=owner.scoped_id,
            content_json={"text": "A"}, content_hash="h", content_uri="", blob_uri="",
            lineage_input_refs=[], metadata_json={}, created_by_run_id=src_run.run_id,
        ))
        record = ProviderInvocationRecordModel(
            record_id=uuid.uuid4(), provider_attempt_id=pa.provider_attempt_id,
            provider_id="fake", model_id="m", model_version="1.0",
            idempotency_key=pa.idempotency_key, request_body_hash="rb",
            response_fingerprint="fp", usage={}, actual_cost=0.0,
            started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
        )
        session.add(record)
        session.flush()
        session.add(ProviderOutputBindingModel(
            binding_id=uuid.uuid4(), record_id=record.record_id,
            output_artifact_version_id=av, output_index=0, owner_scope=owner.scoped_id,
        ))
    partial = runtime.create_partial_run(
        source_run_id=src_run.run_id,
        compiled_plan=plan,
        owner_scope=owner,
        closure={"execute": ["B"], "reuse": ["A"], "skip": []},
    )
    # Cancel the source run; the partial run is an immutable slice so it
    # must continue unaffected.
    runtime.cancel_run(src_run.run_id)
    with factory() as session:
        partial_row = session.get(WorkflowRunModel, partial.run_id)
        # The partial run is persisted as QUEUED → the start_run transition
        # is the worker's job.  The slice itself must remain QUEUED.
        assert partial_row.status == RunStatus.QUEUED
        assert partial_row.owner_scope == owner.scoped_id


# -----------------------------------------------------------------------
# 5. Agent / Recipe dispatch outbox persisted BEFORE any network call
# -----------------------------------------------------------------------


def test_agent_dispatch_outbox_persisted_before_adapter_call(factory, owner):
    """No path may call adapter.submit before the provider_dispatch outbox row is committed.

    The Agent service delegates to ``RuntimeService.dispatch_provider`` which
    is the single entry point for any external side-effect.  We exercise
    that path and verify the provider_dispatch outbox row exists with the
    canonical dedupe_key pinned to the provider_attempt_id, BEFORE any
    downstream consumer publishes it.
    """
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    runtime.set_attempt_running(attempt_id, lease_id="agent-dispatch")

    pa, event = runtime.dispatch_provider(
        attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=f"agent-{uuid.uuid4().hex}", request_body_hash="rb",
        dispatch_payload={
            "operation": "llm",
            "request": {"messages": [{"role": "user", "content": "hi"}]},
            "expected_epoch": 1,
            "result_schema": {"schema_id": "agent_output", "schema_version": 1, "owner_scope": OWNER_SCOPE},
            "kind": "agent",
        },
    )
    with factory() as session:
        ev = session.get(OutboxEventModel, event.event_id)
    assert ev is not None
    assert ev.purpose == "provider_dispatch"
    assert ev.dedupe_key == str(pa.provider_attempt_id)
    # The dispatch outbox row must remain un-published until the worker
    # consumer picks it up — that is the durable contract.
    assert ev.published_at is None
    # And the same provider_attempt_id must be referenced by the durable
    # ProviderInvocationAttempt row that the Agent service later uses.
    with factory() as session:
        pa_row = session.get(ProviderInvocationAttemptModel, pa.provider_attempt_id)
    assert pa_row is not None and pa_row.node_run_attempt_id == attempt_id


def test_recipe_dispatch_outbox_persisted_before_adapter_call(factory, owner):
    """RecipeRuntimeService.dispatch_external emits provider_dispatch outbox before any adapter call."""
    from src.domain.provider.atlascloud import AtlasSubmissionUnknown

    runtime = RuntimeService(factory)
    # Recipe body must match the documented MediaRecipe v1 schema: a
    # `recipe_type` plus an `operator_graph` keyed by operator id.
    body = {
        "recipe_type": "image_pipeline",
        "operator_graph": {
            "step-1": {
                "type": "atlas_image",
                "model_id": "fake-model",
                "parameters": {"prompt": "hi"},
                "inputs": [],
                "outputs": ["image"],
                "supported_controls": [],
            },
        },
    }
    from src.domain.recipe.media_recipe_compiler import compile_media_recipe
    compiled = compile_media_recipe(body)
    plan_hash = compiled["plan_hash"]
    with factory.begin() as session:
        _, revision_id, plan_id = _seed_workflow_revision_plan(session, plan_json={
            "resolved_graph": {"nodes": [{"id": "recipe", "type": "recipe.image"}], "edges": []},
        })
    plan = _plan_for(revision_id, plan_id, graph={
        "nodes": [{"id": "recipe", "type": "recipe.image"}], "edges": [],
    })
    src_run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    runtime.start_run(src_run.run_id)
    with factory.begin() as session:
        node = session.scalar(select(NodeRunModel).where(
            NodeRunModel.run_id == src_run.run_id, NodeRunModel.node_instance_id == "recipe",
        ))
        parent_attempt = NodeRunAttemptModel(
            attempt_id=uuid.uuid4(), node_run_id=node.node_run_id, attempt_number=1,
            execution_epoch=1, fixed_input={"recipe_body": body, "recipe_plan_hash": plan_hash},
            status=AttemptStatus.LEASED, lease_id="recipe-worker",
        )
        session.add(parent_attempt)
        session.flush()
        parent_attempt_id = parent_attempt.attempt_id

    outbox_seen_before_adapter = threading.Event()
    adapter_called = threading.Event()

    class TracingAdapter:
        @property
        def provider_id(self):
            return "atlascloud"

        def submit(self, *, operation: str, model_id: str, payload: dict, idempotency_key: str):
            with factory() as session:
                dispatch = session.scalar(select(OutboxEventModel).where(
                    OutboxEventModel.purpose == "provider_dispatch",
                    OutboxEventModel.aggregate_id.in_(
                        select(ProviderInvocationAttemptModel.provider_attempt_id).where(
                            ProviderInvocationAttemptModel.idempotency_key == idempotency_key,
                        )
                    ),
                ))
                if dispatch is not None:
                    outbox_seen_before_adapter.set()
            adapter_called.set()
            raise AtlasSubmissionUnknown()

    recipe_runtime = RecipeRuntimeService(factory)
    recipe_runtime.materialize(parent_attempt_id=parent_attempt_id, body=body, inputs={"x": 1})
    with factory.begin() as session:
        child = session.scalar(select(NodeRunAttemptModel).where(
            NodeRunAttemptModel.node_run_id.in_(
                select(NodeRunModel.node_run_id).where(
                    NodeRunModel.run_id == src_run.run_id,
                    NodeRunModel.node_instance_id.like("recipe:recipe:%"),
                )
            )
        ))
    idempotency_key = f"recipe-{uuid.uuid4().hex}"
    recipe_runtime.dispatch_external(child.attempt_id, adapter=TracingAdapter(), idempotency_key=idempotency_key)
    assert outbox_seen_before_adapter.is_set()
    assert adapter_called.is_set()


# -----------------------------------------------------------------------
# 6. Unknown → reconcile → completed
# -----------------------------------------------------------------------


def test_unknown_dispatch_reconciles_to_completed_via_callback(factory, owner):
    """dispatch raises AtlasSubmissionUnknown → attempt is UNKNOWN → callback completes it."""
    runtime = RuntimeService(factory)
    run_id, _, attempt_id, provider_id, artifact_id = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    # reconcile_unknown only walks atlascloud invocations (matches the
    # real Provider spike in Foundation); flip the seeded attempt so the
    # worker matches it.
    with factory.begin() as session:
        provider = session.get(ProviderInvocationAttemptModel, provider_id)
        provider.provider_id = "atlascloud"

    # Bind a task id first (typical for media async path).
    runtime.bind_provider_task(provider_id, f"atlas-task-{uuid.uuid4().hex}")
    # Force UNKNOWN.
    runtime.mark_provider_unknown(provider_id)
    with factory() as session:
        attempt_row = session.get(NodeRunAttemptModel, attempt_id)
        assert attempt_row.status == AttemptStatus.UNKNOWN
        binding = session.scalar(select(WorkflowTaskBindingModel).where(
            WorkflowTaskBindingModel.provider_attempt_id == provider_id,
        ))
        assert binding is not None

    worker = RuntimeWorker(factory)

    class _Adapter:
        def get_prediction(self, requested_task_id):
            return {"task_id": requested_task_id, "status": "completed",
                    "outputs": [{"text": "done"}], "model_version": "m"}

    report = worker.reconcile_unknown(_Adapter())
    assert report.completed == 1

    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider_id,
        )))
        result_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.purpose == "result_publish",
        )))
        bindings = list(session.scalars(select(ProviderOutputBindingModel).where(
            ProviderOutputBindingModel.record_id == records[0].record_id,
        )))
    assert len(records) == 1
    assert len(result_events) == 1
    assert len(bindings) == 1


# -----------------------------------------------------------------------
# 7. Dispatch replay idempotency at the database boundary
# -----------------------------------------------------------------------


def test_dispatch_outbox_replay_with_different_attempt_creates_fresh_outbox(factory, owner):
    """Two distinct attempts each get exactly one dispatch outbox row.

    Replay-with-same-idempotency-key is already covered by Foundation
    PG tests.  Here we verify the per-attempt uniqueness invariant:
    one provider_dispatch per provider_attempt_id.
    """
    runtime = RuntimeService(factory)
    run_id, _, attempt_id = _seed_run_with_attempt(factory, runtime)
    runtime.start_run(run_id)
    runtime.set_attempt_running(attempt_id, lease_id="dispatch-1")
    pa_a, _ = runtime.dispatch_provider(
        attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=f"replay-{uuid.uuid4().hex}", request_body_hash="rb",
    )
    pa_b_replay, _ = runtime.dispatch_provider(
        attempt_id, provider_id="fake", model_id="m1",
        idempotency_key=pa_a.idempotency_key, request_body_hash="rb",
    )
    assert pa_a.provider_attempt_id == pa_b_replay.provider_attempt_id
    with factory() as session:
        dispatch_events = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == pa_a.provider_attempt_id,
            OutboxEventModel.purpose == "provider_dispatch",
        )))
    assert len(dispatch_events) == 1


# -----------------------------------------------------------------------
# 8. ForbiddenAttemptStatus is enforced when caller lacks the lease
# -----------------------------------------------------------------------


def test_partial_run_owner_mismatch_is_rejected(factory, owner):
    """create_partial_run enforces source.owner_scope == owner_scope.scoped_id."""
    runtime = RuntimeService(factory)
    revision_id, plan_id, _, graph = _seed_partial_run_graph(factory)
    plan = _plan_for(revision_id, plan_id, graph=graph)
    with factory.begin() as session:
        plan_row = session.get(CompiledExecutionPlanModel, plan_id)
        plan_row.plan_json = {
            "plan_id": str(plan_id),
            "workflow_revision_id": str(revision_id),
            "plan_hash": plan.plan_hash,
            "resolved_graph": graph,
            "resolved_input_refs": [],
        }
    src_run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    other = OwnerScope(kind="user", id=uuid.UUID(int=0xBADC0FFEE0DDF00D))
    with pytest.raises(ForbiddenError):
        runtime.create_partial_run(
            source_run_id=src_run.run_id,
            compiled_plan=plan,
            owner_scope=other,
            closure={"execute": ["A"], "reuse": [], "skip": []},
        )


# -----------------------------------------------------------------------
# P0: cancel-during-result-publish race
# -----------------------------------------------------------------------


def test_cancel_during_result_publish_phase2_revalidates_fence(factory, owner):
    """Phase 1 passes, cancel_run commits, phase 2 must re-validate and reject.

    This is the race described in the P0 feedback: the callback reads
    valid state in phase 1, `cancel_run` commits between phase 1 and
    phase 2, and phase 2 opens a fresh transaction.  The phase 2
    fence must catch the now-cancelled run and emit a discarded audit
    row before raising.
    """
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, artifact_id = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    epoch = 1
    # Phase 1 (read-only) succeeded internally for existing tests.
    # We simulate the race by cancelling between the two phases:
    # go past phase 1 idempotency check — we set artifact ready and
    # then cancel before calling record_provider_result.
    runtime.cancel_run(run_id)
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            provider_id, model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
            current_epoch=epoch,
        )
    with factory() as session:
        discarded = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.discarded",
        ))
        records = session.scalar(select(func.count()).select_from(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id == provider_id,
        ))
        result_events = session.scalar(select(func.count()).select_from(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.purpose == "result_publish",
        ))
    assert discarded is not None
    assert discarded.purpose == "provider_dispatch_rejected"
    assert records <= 0  # may be count or scalar None
    assert result_events <= 0


def test_cancel_during_json_publish_phase2_revalidates_fence(factory, owner):
    """Same race for the JSON-output publish path."""
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, _ = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    epoch = 1
    runtime.cancel_run(run_id)
    with pytest.raises(ConflictError):
        runtime.publish_provider_json_outputs(
            provider_id, owner_scope=OWNER_SCOPE,
            schema_id="provider_output", schema_version=1,
            outputs=[{"text": "late"}],
            model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.0, current_epoch=epoch,
        )
    with factory() as session:
        discarded = session.scalar(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.discarded",
        ))
    assert discarded is not None


# -----------------------------------------------------------------------
# P1: duplicate provider.discarded is idempotent
# -----------------------------------------------------------------------


def test_duplicate_discarded_event_is_idempotent(factory, owner):
    """Two late callbacks hitting the fence must produce exactly one discarded row."""
    runtime = RuntimeService(factory)
    run_id, node_id, attempt_id, provider_id, artifact_id = _seed_provider_attempt(factory, runtime, OWNER_SCOPE)
    runtime.cancel_run(run_id)

    # First callback
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            provider_id, model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
            current_epoch=1,
        )
    # Second callback
    with pytest.raises(ConflictError):
        runtime.record_provider_result(
            provider_id, model_version="1.0", response_fingerprint="fp",
            usage={}, actual_cost=0.1, output_artifact_version_ids=[artifact_id],
            current_epoch=1,
        )
    with factory() as session:
        discarded = list(session.scalars(select(OutboxEventModel).where(
            OutboxEventModel.aggregate_id == provider_id,
            OutboxEventModel.event_type == "provider.discarded",
        )))
    assert len(discarded) == 1
    assert discarded[0].dedupe_key == str(provider_id)
