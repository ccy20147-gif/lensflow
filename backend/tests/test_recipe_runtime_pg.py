"""PG contracts for durable Media Recipe operator expansion."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text

from src.domain.provider.atlascloud import AtlasCloudAdapter
from src.domain.recipe.recipe_runtime import RecipeRuntimeService
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.models import (
    NodeRunAttemptModel,
    NodeRunModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    ProviderInvocationAttemptModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1",
)


@pytest.fixture
def factory():
    value = get_session_factory()
    with value() as session:
        session.execute(text("SELECT 1"))
    return value


def _parent(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    workflow = SqlWorkflowService(factory).create_workflow(owner_scope=owner)
    with factory.begin() as session:
        now = datetime.now(timezone.utc)
        revision = WorkflowRevisionModel(
            revision_id=uuid4(), workflow_id=workflow.workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(),
            graph={}, config={}, layout={}, revision_status=RevisionStatus.ACTIVE,
            created_at=now,
        )
        session.add(revision)
        session.flush()
        run = WorkflowRunModel(
            run_id=uuid4(), workflow_revision_id=revision.revision_id,
            compiled_plan_id=uuid4(), owner_scope=owner.scoped_id,
            input_snapshot={}, status=RunStatus.RUNNING, created_at=now,
        )
        session.add(run)
        session.flush()
        node = NodeRunModel(
            node_run_id=uuid4(), run_id=run.run_id, node_instance_id="recipe",
            node_type_id="recipe", status=NodeRunStatus.RUNNING,
        )
        session.add(node)
        session.flush()
        attempt = NodeRunAttemptModel(
            attempt_id=uuid4(), node_run_id=node.node_run_id,
            status=AttemptStatus.RUNNING, fixed_input={},
        )
        session.add(attempt)
    return attempt.attempt_id


def test_multistep_recipe_materializes_and_collects_failure(factory):
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "image", "operator_graph": {
        "input": {"type": "input"},
        "resize": {"type": "resize", "inputs": ["input.x"]},
        "convert": {"type": "format_convert", "inputs": ["resize.x"]},
    }}
    children = service.materialize(parent_attempt_id=parent, body=body, inputs={"x": "a"})
    assert len(children) == 3
    service.complete_internal(children[0])
    service.fail_child(children[1], policy="collect_errors")
    fallback = service.fallback_attempt(children[1])
    with factory() as session:
        assert session.get(NodeRunAttemptModel, parent).status == AttemptStatus.WAITING_EXTERNAL
        assert session.get(NodeRunAttemptModel, fallback).execution_epoch == 2


def test_external_unknown_does_not_blind_retry(factory):
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "image", "operator_graph": {
        "input": {"type": "input"},
        "generate": {"type": "atlas_image", "model_id": "m", "inputs": ["input.x"]},
    }}
    children = service.materialize(parent_attempt_id=parent, body=body, inputs={"x": "a"})

    class Broken:
        def request(self, *args, **kwargs):
            raise httpx.ConnectError("lost")

    adapter = AtlasCloudAdapter(transport=Broken(), api_key="key", base_url="https://atlas.test")
    result = service.dispatch_external(children[1], adapter=adapter, idempotency_key=f"unknown-{uuid4()}")
    assert result["status"] == "unknown"
    with factory() as session:
        rows = list(session.query(ProviderInvocationAttemptModel).filter(
            ProviderInvocationAttemptModel.node_run_attempt_id == children[1],
        ))
        assert len(rows) == 1 and rows[0].status == AttemptStatus.UNKNOWN


def test_all_internal_operators_aggregate_parent(factory):
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "x", "operator_graph": {
        "a": {"type": "input"},
        "b": {"type": "resize", "inputs": ["a.x"]},
        "c": {"type": "format_convert", "inputs": ["b.x"]},
    }}
    children = service.materialize(parent_attempt_id=parent, body=body, inputs={})
    for child in children:
        service.complete_internal(child)
    with factory() as session:
        assert session.get(NodeRunAttemptModel, parent).status == AttemptStatus.COMPLETED


def test_diamond_dag_waits_for_every_frozen_predecessor(factory):
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "x", "operator_graph": {
        "source": {"type": "input"},
        "left": {"type": "resize", "inputs": ["source.output"]},
        "right": {"type": "crop", "inputs": ["source.output"]},
        "merge": {"type": "merge", "inputs": ["left.output", "right.output"]},
    }}
    children = service.materialize(parent_attempt_id=parent, body=body, inputs={})
    with factory() as session:
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[0]).node_run_id).status == NodeRunStatus.READY
        assert all(session.get(NodeRunModel, session.get(NodeRunAttemptModel, child).node_run_id).status == NodeRunStatus.PENDING for child in children[1:])
    service.complete_internal(children[0])
    with factory() as session:
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[1]).node_run_id).status == NodeRunStatus.READY
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[2]).node_run_id).status == NodeRunStatus.READY
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[3]).node_run_id).status == NodeRunStatus.PENDING
    service.complete_internal(children[1])
    with factory() as session:
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[3]).node_run_id).status == NodeRunStatus.PENDING
    service.complete_internal(children[2])
    with factory() as session:
        assert session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[3]).node_run_id).status == NodeRunStatus.READY
    service.complete_internal(children[3])
    with factory() as session:
        assert session.get(NodeRunAttemptModel, parent).status == AttemptStatus.COMPLETED


def test_fallback_never_retries_an_unreconciled_unknown(factory):
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "x", "operator_graph": {"a": {"type": "input"}}}
    child = service.materialize(parent_attempt_id=parent, body=body, inputs={})[0]
    with factory.begin() as session:
        session.get(NodeRunAttemptModel, child).status = AttemptStatus.UNKNOWN
    with pytest.raises(Exception, match="reconciliation"):
        service.fallback_attempt(child)


def test_parallel_external_multi_output_children_converge_deterministically(factory):
    """Reverse completion order cannot unlock a merge until both operators finish."""
    parent = _parent(factory)
    service = RecipeRuntimeService(factory)
    body = {"recipe_type": "x", "operator_graph": {
        "left": {"type": "atlas_image", "model_id": "left"},
        "right": {"type": "atlas_image", "model_id": "right"},
        "merge": {"type": "merge", "inputs": ["left.output", "right.output"]},
    }}
    children = service.materialize(parent_attempt_id=parent, body=body, inputs={})
    runtime = RuntimeService(factory)
    with factory() as session:
        parent_attempt = session.get(NodeRunAttemptModel, parent)
        parent_node = session.get(NodeRunModel, parent_attempt.node_run_id)
        owner_scope = session.get(WorkflowRunModel, parent_node.run_id).owner_scope
    right, _ = runtime.dispatch_provider(children[1], provider_id="atlascloud", model_id="right", idempotency_key=f"right-{uuid4()}", request_body_hash="right")
    right_outputs = service.publish_external_result(provider_attempt_id=right.provider_attempt_id, owner_scope=owner_scope, outputs=[{"slot": 1}, {"slot": 2}], model_version="right", fingerprint="right", usage={}, cost=0.2)
    with factory() as session:
        merge = session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[2]).node_run_id)
        assert merge.status == NodeRunStatus.PENDING
    left, _ = runtime.dispatch_provider(children[0], provider_id="atlascloud", model_id="left", idempotency_key=f"left-{uuid4()}", request_body_hash="left")
    left_outputs = service.publish_external_result(provider_attempt_id=left.provider_attempt_id, owner_scope=owner_scope, outputs=[{"slot": "a"}, {"slot": "b"}], model_version="left", fingerprint="left", usage={}, cost=0.2)
    with factory() as session:
        merge = session.get(NodeRunModel, session.get(NodeRunAttemptModel, children[2]).node_run_id)
        assert merge.status == NodeRunStatus.READY
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(
            ProviderInvocationRecordModel.provider_attempt_id.in_([left.provider_attempt_id, right.provider_attempt_id]),
        )))
        assert len(records) == 2
        bindings = [binding for record in records for binding in session.scalars(select(ProviderOutputBindingModel).where(ProviderOutputBindingModel.record_id == record.record_id))]
        assert len(bindings) == 4
        assert {binding.output_artifact_version_id for binding in bindings} == set(left_outputs + right_outputs)
    service.complete_internal(children[2])
    with factory() as session:
        assert session.get(NodeRunAttemptModel, parent).status == AttemptStatus.COMPLETED
