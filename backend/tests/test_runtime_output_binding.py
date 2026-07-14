"""
Test for ProviderOutputBinding persistence (TF-WF-006 FR-16, FR-17)
"""
from __future__ import annotations

import uuid
import pytest

from src.domain.runtime.runtime_service import RuntimeService
from src.schemas.enums import AttemptStatus
from src.schemas.models import (
    CompiledExecutionPlan,
    OwnerScope,
    RegistrySnapshot,
)


@pytest.fixture
def runtime():
    return RuntimeService()


@pytest.fixture
def plan():
    return CompiledExecutionPlan(
        plan_id=uuid.uuid4(),
        workflow_revision_id=uuid.uuid4(),
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid.uuid4()),
        resolved_graph={
            "nodes": [{"id": "n1", "type": "brief"}, {"id": "n2", "type": "generate"}],
            "edges": [],
        },
        plan_hash="h1",
    )


@pytest.fixture
def owner():
    return OwnerScope(kind="user", id=uuid.uuid4())


class TestProviderOutputBinding:
    """TF-WF-006 FR-16, FR-17: Output bindings are saved and queryable."""

    def test_single_output_binding_saved(self, runtime, plan, owner):
        """One output binding is stored and retrievable."""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        runtime.set_attempt_running(attempt.attempt_id, lease_id="l1")

        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id,
            provider_id="p1", model_id="m1",
            idempotency_key="ik1", request_body_hash="rb1",
        )

        av_id = uuid.uuid4()
        record, outbox = runtime.record_provider_result(
            provider_attempt.provider_attempt_id,
            model_version="1.0",
            response_fingerprint="fp1",
            output_artifact_version_ids=[av_id],
            current_epoch=attempt.execution_epoch,
        )

        assert len(runtime._output_bindings) == 1
        binding = list(runtime._output_bindings.values())[0]
        assert binding.record_id == record.record_id
        assert binding.output_artifact_version_id == av_id
        assert binding.output_index == 0

    def test_multiple_output_bindings_saved(self, runtime, plan, owner):
        """Multiple outputs from one ProviderInvocationRecord are all saved."""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        runtime.set_attempt_running(attempt.attempt_id, lease_id="l2")
        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id, provider_id="p1", model_id="m1",
            idempotency_key="ik2", request_body_hash="rb1",
        )

        av_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        record, _ = runtime.record_provider_result(
            provider_attempt.provider_attempt_id,
            model_version="1.0", response_fingerprint="fp2",
            output_artifact_version_ids=av_ids,
            current_epoch=attempt.execution_epoch,
        )

        assert len(runtime._output_bindings) == 3
        bindings = sorted(runtime._output_bindings.values(), key=lambda b: b.output_index)
        for i, b in enumerate(bindings):
            assert b.record_id == record.record_id
            assert b.output_artifact_version_id == av_ids[i]
            assert b.output_index == i

    def test_provider_attempt_marked_completed(self, runtime, plan, owner):
        """Provider attempt transitions to COMPLETED after result recorded."""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        runtime.set_attempt_running(attempt.attempt_id, lease_id="l3")
        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id, provider_id="p1", model_id="m1",
            idempotency_key="ik3", request_body_hash="rb1",
        )
        runtime.record_provider_result(
            provider_attempt.provider_attempt_id,
            model_version="1.0", response_fingerprint="fp3",
            output_artifact_version_ids=[uuid.uuid4()],
            current_epoch=attempt.execution_epoch,
        )

        assert provider_attempt.status == AttemptStatus.COMPLETED

    def test_outbox_events_for_result(self, runtime, plan, owner):
        """Result publish outbox event is created alongside bindings."""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        runtime.set_attempt_running(attempt.attempt_id, lease_id="l4")
        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id, provider_id="p1", model_id="m1",
            idempotency_key="ik4", request_body_hash="rb1",
        )

        runtime.record_provider_result(
            provider_attempt.provider_attempt_id,
            model_version="1.0", response_fingerprint="fp4",
            output_artifact_version_ids=[uuid.uuid4(), uuid.uuid4()],
            current_epoch=attempt.execution_epoch,
        )

        result_outboxes = [e for e in runtime._outbox if e.purpose == "result_publish"]
        assert len(result_outboxes) >= 1

    def test_epoch_fencing_rejects_stale_output(self, runtime, plan, owner):
        """Output binding creation fails when execution epoch doesn't match."""
        run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
        runtime.start_run(run.run_id)
        node_run = list(runtime._node_runs.values())[0]
        attempt = runtime.create_attempt(node_run.node_run_id)
        provider_attempt, _ = runtime.dispatch_provider(
            attempt.attempt_id, provider_id="p1", model_id="m1",
            idempotency_key="ik5", request_body_hash="rb1",
        )

        with pytest.raises(Exception):
            runtime.record_provider_result(
                provider_attempt.provider_attempt_id,
                model_version="1.0", response_fingerprint="fp5",
                output_artifact_version_ids=[uuid.uuid4()],
                current_epoch=99,  # wrong epoch
            )
