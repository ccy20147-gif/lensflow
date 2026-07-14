"""PG-backed contract tests for Control Flow persistence (TF-CF-001).

All state lives in PostgreSQL.  Run explicitly with ``TOONFLOW_RUN_PG_TESTS=1``
after ``alembic upgrade head``.

Tests:
- Condition CRUD, static validation, evaluate-and-persist
- Join CRUD, static validation, resolve
- MapItem lifecycle: PENDING → RUNNING → COMPLETED/FAILED/SKIPPED
- ForEach / Subworkflow stubs (config storage only)
"""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.domain.workflow.control_flow_service import (
    ControlFlowService,
    DefaultConditionEvaluator,
    validate_condition_config,
    validate_join_config,
    validate_map_item_config,
)
from src.infra.db.models import (
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import (
    ConditionOperator,
    ForEachMode,
    JoinStrategy,
    MapItemStatus,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def factory():
    return get_session_factory()


@pytest.fixture
def cf(factory):
    return ControlFlowService(session_factory=factory)


@pytest.fixture
def run_id(factory) -> uuid.UUID:
    """Create a minimal Workflow + WorkflowRevision + WorkflowRun so FKs resolve."""
    wid = uuid.uuid4()
    revision_id = uuid.uuid4()
    rid = uuid.uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=wid, owner_scope="user:test"))
        session.add(WorkflowRevisionModel(
            revision_id=revision_id, workflow_id=wid, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid.uuid4(),
        ))
        session.add(WorkflowRunModel(
            run_id=rid, workflow_revision_id=revision_id,
            compiled_plan_id=uuid.uuid4(), owner_scope="user:test",
        ))
    return rid


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------


class TestValidateConditionConfig:
    """Static validation on Condition configuration (not runtime evaluation)."""

    def test_valid_eq(self):
        validate_condition_config(operator=ConditionOperator.EQ, threshold=42)

    def test_valid_exists(self):
        validate_condition_config(operator=ConditionOperator.EXISTS, threshold=None)

    def test_invalid_operator(self):
        with pytest.raises(ValidationError_):
            validate_condition_config(operator="bogus")  # type: ignore[arg-type]

    def test_missing_threshold_for_comparison(self):
        with pytest.raises(ValidationError_):
            validate_condition_config(operator=ConditionOperator.GT, threshold=None)

    def test_in_op_requires_list(self):
        with pytest.raises(ValidationError_):
            validate_condition_config(operator=ConditionOperator.IN_OP, threshold="not_a_list")

    def test_exists_with_threshold(self):
        with pytest.raises(ValidationError_):
            validate_condition_config(operator=ConditionOperator.EXISTS, threshold=42)


class TestValidateJoinConfig:
    """Static validation on Join configuration."""

    def test_valid_and(self):
        validate_join_config(strategy=JoinStrategy.AND, source_node_ids=["a", "b"])

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError_):
            validate_join_config(strategy="bogus", source_node_ids=["a", "b"])  # type: ignore[arg-type]

    def test_too_few_sources(self):
        with pytest.raises(ValidationError_):
            validate_join_config(strategy=JoinStrategy.OR, source_node_ids=["a"])

    def test_empty_sources(self):
        with pytest.raises(ValidationError_):
            validate_join_config(strategy=JoinStrategy.AND, source_node_ids=[])


class TestValidateMapItemConfig:
    """Static validation on MapItem configuration."""

    def test_valid(self):
        validate_map_item_config(item_key="item1")

    def test_empty_key(self):
        with pytest.raises(ValidationError_):
            validate_map_item_config(item_key="")


# ---------------------------------------------------------------------------
# Condition persistence
# ---------------------------------------------------------------------------


class TestConditionPersistence:
    """Condition CRUD — all state in PostgreSQL."""

    def test_create_and_get(self, cf, run_id):
        condition = cf.create_condition(
            run_id,
            node_instance_id="cond_1",
            operator=ConditionOperator.GT,
            threshold=100,
            value_path="$.score",
            config={"label": "Score > 100"},
        )
        assert condition.condition_id is not None
        assert condition.node_instance_id == "cond_1"
        assert condition.operator == ConditionOperator.GT
        assert condition.status == "pending"

        fetched = cf.get_condition(condition.condition_id)
        assert fetched.condition_id == condition.condition_id
        assert fetched.threshold == 100
        assert fetched.value_path == "$.score"

    def test_create_duplicate_raises(self, cf, run_id):
        cf.create_condition(run_id, node_instance_id="cond_dup", operator=ConditionOperator.EQ, threshold=1)
        with pytest.raises(ConflictError):
            cf.create_condition(run_id, node_instance_id="cond_dup", operator=ConditionOperator.EQ, threshold=2)

    def test_get_not_found(self, cf):
        with pytest.raises(NotFoundError):
            cf.get_condition(uuid.uuid4())

    def test_list_conditions(self, cf, run_id):
        cf.create_condition(run_id, node_instance_id="c1", operator=ConditionOperator.EQ, threshold=1)
        cf.create_condition(run_id, node_instance_id="c2", operator=ConditionOperator.GT, threshold=10)
        conditions = cf.list_conditions(run_id)
        assert len(conditions) == 2
        assert {c.node_instance_id for c in conditions} == {"c1", "c2"}

    def test_evaluate_and_persist(self, cf, run_id):
        condition = cf.create_condition(
            run_id, node_instance_id="eval_test",
            operator=ConditionOperator.GT, threshold=50,
        )
        result = cf.evaluate_condition(condition.condition_id, resolved_value=75)
        assert result is True

        # Verify persisted
        fetched = cf.get_condition(condition.condition_id)
        assert fetched.result is True
        assert fetched.status == "evaluated"

    def test_default_evaluator(self):
        evaluator = DefaultConditionEvaluator()
        assert evaluator.evaluate(operator=ConditionOperator.EQ, resolved_value=42, threshold=42) is True
        assert evaluator.evaluate(operator=ConditionOperator.EQ, resolved_value=41, threshold=42) is False
        assert evaluator.evaluate(operator=ConditionOperator.GT, resolved_value=10, threshold=5) is True
        assert evaluator.evaluate(operator=ConditionOperator.IN_OP, resolved_value="a", threshold=["a", "b"]) is True
        assert evaluator.evaluate(operator=ConditionOperator.IN_OP, resolved_value="c", threshold=["a", "b"]) is False
        assert evaluator.evaluate(operator=ConditionOperator.EXISTS, resolved_value="something", threshold=None) is True
        assert evaluator.evaluate(operator=ConditionOperator.EXISTS, resolved_value=None, threshold=None) is False
        assert evaluator.evaluate(operator=ConditionOperator.CONTAINS, resolved_value="hello world", threshold="world") is True

    def test_persist_across_service_instance(self, cf, run_id, factory):
        """Verify data survives when loaded by a new service instance."""
        condition = cf.create_condition(
            run_id, node_instance_id="cross_instance",
            operator=ConditionOperator.EQ, threshold=1,
        )
        cf.evaluate_condition(condition.condition_id, resolved_value=1)

        # New service instance — no process-local state
        cf2 = ControlFlowService(session_factory=factory)
        fetched = cf2.get_condition(condition.condition_id)
        assert fetched.result is True
        assert fetched.status == "evaluated"


# ---------------------------------------------------------------------------
# Join persistence
# ---------------------------------------------------------------------------


class TestJoinPersistence:
    """Join CRUD — all state in PostgreSQL."""

    def test_create_and_get(self, cf, run_id):
        join = cf.create_join(
            run_id,
            node_instance_id="join_1",
            strategy=JoinStrategy.AND,
            source_node_ids=["branch_a", "branch_b"],
            config={"merge_key": "result"},
        )
        assert join.join_id is not None
        assert join.strategy == JoinStrategy.AND
        assert join.source_node_ids == ["branch_a", "branch_b"]
        assert join.status == "pending"

        fetched = cf.get_join(join.join_id)
        assert fetched.join_id == join.join_id

    def test_create_duplicate_raises(self, cf, run_id):
        cf.create_join(run_id, node_instance_id="jdup", strategy=JoinStrategy.OR, source_node_ids=["a", "b"])
        with pytest.raises(ConflictError):
            cf.create_join(run_id, node_instance_id="jdup", strategy=JoinStrategy.AND, source_node_ids=["c", "d"])

    def test_get_not_found(self, cf):
        with pytest.raises(NotFoundError):
            cf.get_join(uuid.uuid4())

    def test_list_joins(self, cf, run_id):
        cf.create_join(run_id, node_instance_id="j1", strategy=JoinStrategy.AND, source_node_ids=["a", "b"])
        cf.create_join(run_id, node_instance_id="j2", strategy=JoinStrategy.OR, source_node_ids=["c", "d"])
        joins = cf.list_joins(run_id)
        assert len(joins) == 2

    def test_resolve_join(self, cf, run_id):
        join = cf.create_join(
            run_id, node_instance_id="resolve_join",
            strategy=JoinStrategy.AND, source_node_ids=["a", "b"],
        )
        result = {"merged": True, "values": [1, 2]}
        updated = cf.update_join_result(join.join_id, result=result)
        assert updated.status == "completed"
        assert updated.result == result

    def test_persist_across_service_instance(self, cf, run_id, factory):
        join = cf.create_join(
            run_id, node_instance_id="ji_cross",
            strategy=JoinStrategy.XOR, source_node_ids=["x", "y"],
        )
        cf.update_join_result(join.join_id, result={"winner": "x"})

        cf2 = ControlFlowService(session_factory=factory)
        fetched = cf2.get_join(join.join_id)
        assert fetched.status == "completed"
        assert fetched.result == {"winner": "x"}


# ---------------------------------------------------------------------------
# MapItem lifecycle
# ---------------------------------------------------------------------------


class TestMapItemLifecycle:
    """MapItem state transitions: PENDING → RUNNING → COMPLETED/FAILED/SKIPPED."""

    def test_create_and_get(self, cf, run_id):
        item = cf.create_map_item(
            run_id,
            node_instance_id="foreach_1",
            item_key="item_001",
            item_value={"name": "Alice"},
        )
        assert item.map_item_id is not None
        assert item.item_key == "item_001"
        assert item.status == MapItemStatus.PENDING

        fetched = cf.get_map_item(item.map_item_id)
        assert fetched.item_value == {"name": "Alice"}

    def test_create_duplicate_raises(self, cf, run_id):
        cf.create_map_item(run_id, node_instance_id="fe", item_key="dup")
        with pytest.raises(ConflictError):
            cf.create_map_item(run_id, node_instance_id="fe", item_key="dup")

    def test_get_not_found(self, cf):
        with pytest.raises(NotFoundError):
            cf.get_map_item(uuid.uuid4())

    def test_list_map_items(self, cf, run_id):
        cf.create_map_item(run_id, node_instance_id="fe", item_key="a")
        cf.create_map_item(run_id, node_instance_id="fe", item_key="b")
        cf.create_map_item(run_id, node_instance_id="other", item_key="c")
        all_items = cf.list_map_items(run_id)
        assert len(all_items) == 3
        filtered = cf.list_map_items(run_id, node_instance_id="fe")
        assert len(filtered) == 2

    def test_full_lifecycle_complete(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="lifecycle")
        assert item.status == MapItemStatus.PENDING

        started = cf.start_map_item(item.map_item_id)
        assert started.status == MapItemStatus.RUNNING
        assert started.started_at is not None

        completed = cf.complete_map_item(started.map_item_id, result={"output": "done"})
        assert completed.status == MapItemStatus.COMPLETED
        assert completed.completed_at is not None
        assert completed.result == {"output": "done"}

    def test_full_lifecycle_fail(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="fail_me")
        cf.start_map_item(item.map_item_id)
        failed = cf.fail_map_item(item.map_item_id, error="Something went wrong")
        assert failed.status == MapItemStatus.FAILED
        assert failed.error == "Something went wrong"

    def test_full_lifecycle_skip(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="skip_me")
        skipped = cf.skip_map_item(item.map_item_id)
        assert skipped.status == MapItemStatus.SKIPPED

    def test_cannot_start_non_pending(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="no_start")
        cf.start_map_item(item.map_item_id)
        with pytest.raises(ConflictError):
            cf.start_map_item(item.map_item_id)

    def test_cannot_complete_non_running(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="no_complete")
        with pytest.raises(ConflictError):
            cf.complete_map_item(item.map_item_id, result={})

    def test_cannot_fail_non_running(self, cf, run_id):
        item = cf.create_map_item(run_id, node_instance_id="fe", item_key="no_fail")
        cf.complete_map_item(cf.start_map_item(item.map_item_id).map_item_id, result={})
        with pytest.raises(ConflictError):
            cf.fail_map_item(item.map_item_id, error="nope")

    def test_skip_allowed_from_pending_or_running(self, cf, run_id):
        # Skip from PENDING
        item_p = cf.create_map_item(run_id, node_instance_id="fe", item_key="skip_pending")
        assert cf.skip_map_item(item_p.map_item_id).status == MapItemStatus.SKIPPED

        # Skip from RUNNING
        item_r = cf.create_map_item(run_id, node_instance_id="fe", item_key="skip_running")
        cf.start_map_item(item_r.map_item_id)
        assert cf.skip_map_item(item_r.map_item_id).status == MapItemStatus.SKIPPED

        # Cannot skip from COMPLETED
        item_c = cf.create_map_item(run_id, node_instance_id="fe", item_key="skip_completed")
        cf.complete_map_item(cf.start_map_item(item_c.map_item_id).map_item_id, result={})
        with pytest.raises(ConflictError):
            cf.skip_map_item(item_c.map_item_id)


# ---------------------------------------------------------------------------
# Bounded Map / OrderedMap / Fold
# ---------------------------------------------------------------------------


class TestBoundedMap:
    """Map scheduling is bounded, ordered and restart-safe."""

    def test_create_and_get(self, cf, run_id):
        fe = cf.create_for_each(
            run_id,
            node_instance_id="fe_stub",
            mode=ForEachMode.PARALLEL,
            collection_ref="$.items",
            item_count=5,
            config={"max_items": 10, "max_concurrency": 2, "failure_policy": "collect_errors"},
        )
        assert fe.for_each_id is not None
        assert fe.mode == ForEachMode.PARALLEL
        assert fe.item_count == 5
        assert fe.status == "pending"

        fetched = cf.get_for_each(fe.for_each_id)
        assert fetched.collection_ref == "$.items"

    def test_list_for_each(self, cf, run_id):
        cf.create_for_each(run_id, node_instance_id="fe1")
        cf.create_for_each(run_id, node_instance_id="fe2")
        items = cf.list_for_each(run_id)
        assert len(items) == 2

    def test_parallel_outputs_remain_input_order(self, cf, run_id):
        fe = cf.create_for_each(
            run_id, node_instance_id="map", mode=ForEachMode.PARALLEL,
            config={"items": [{"v": 0}, {"v": 1}, {"v": 2}], "max_items": 3,
                    "max_concurrency": 3, "failure_policy": "fail_fast"},
        )
        claimed = cf.claim_map_items(fe.for_each_id)
        assert [item.item_index for item in claimed] == [0, 1, 2]
        for item in reversed(claimed):
            cf.complete_map_item(item.map_item_id, result={"index": item.item_index})
        assert cf.ordered_map_output(fe.for_each_id) == [{"index": 0}, {"index": 1}, {"index": 2}]

    def test_fold_persists_checkpoint_and_continues(self, cf, run_id, factory):
        fe = cf.create_for_each(
            run_id, node_instance_id="fold", mode=ForEachMode.SEQUENTIAL,
            config={"items": [{"v": 0}, {"v": 1}], "max_items": 2, "max_concurrency": 1,
                    "failure_policy": "fail_fast", "fold": True},
        )
        first = cf.claim_map_items(fe.for_each_id)[0]
        cf.complete_map_item(first.map_item_id, result={"v": 0})
        assert cf.get_for_each(fe.for_each_id).config["checkpoint_index"] == 0
        restarted = ControlFlowService(session_factory=factory)
        second = restarted.claim_map_items(fe.for_each_id)[0]
        assert second.item_index == 1
        restarted.complete_map_item(second.map_item_id, result={"v": 1})
        assert restarted.get_for_each(fe.for_each_id).config["accumulator"] == [{"v": 0}, {"v": 1}]

    def test_concurrent_claims_never_exceed_frozen_concurrency(self, cf, run_id, factory):
        flow = cf.create_for_each(
            run_id, node_instance_id="race", mode=ForEachMode.PARALLEL,
            config={"items": [{"v": index} for index in range(4)], "max_items": 4,
                    "max_concurrency": 2, "failure_policy": "fail_fast"},
        )
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait(timeout=5)
            return ControlFlowService(session_factory=factory).claim_map_items(flow.for_each_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.submit(claim), pool.submit(claim)
            claimed = left.result(timeout=5) + right.result(timeout=5)
        assert len(claimed) == 2
        assert len({item.map_item_id for item in claimed}) == 2


# ---------------------------------------------------------------------------
# Fixed-revision SubworkflowCall
# ---------------------------------------------------------------------------


class TestSubworkflow:
    """A subworkflow is a pinned child run, never a configuration-only stub."""

    @staticmethod
    def _child_revision(factory) -> uuid.UUID:
        workflow_id, revision_id = uuid.uuid4(), uuid.uuid4()
        with factory.begin() as session:
            session.add(WorkflowModel(workflow_id=workflow_id, owner_scope="user:test"))
            session.add(WorkflowRevisionModel(
                revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
                graph_hash="child-g", execution_hash="child-e", registry_snapshot_id=uuid.uuid4(),
                graph={"nodes": [{"id": "child-node", "type": "work"}], "edges": []},
            ))
        return revision_id

    def test_create_and_get(self, cf, run_id):
        sw = cf.create_subworkflow(
            run_id,
            node_instance_id="sw_stub",
            parent_node_instance_id="parent_node_1",
            config={"workflow_revision_id": str(self._child_revision(cf._session_factory)),
                    "input_mapping": {"input": "parent.output"}, "output_mapping": {"output": "result"},
                    "depth": 1, "max_depth": 3},
        )
        assert sw.subworkflow_id is not None
        assert sw.parent_node_instance_id == "parent_node_1"
        assert sw.status == "pending"
        assert sw.child_run_id is not None

        fetched = cf.get_subworkflow(sw.subworkflow_id)
        assert fetched.config["depth"] == 1

    def test_list_subworkflows(self, cf, run_id):
        cf.create_subworkflow(run_id, node_instance_id="sw1", parent_node_instance_id="p1", config={"workflow_revision_id": str(self._child_revision(cf._session_factory)), "input_mapping": {}, "output_mapping": {}})
        cf.create_subworkflow(run_id, node_instance_id="sw2", parent_node_instance_id="p2", config={"workflow_revision_id": str(self._child_revision(cf._session_factory)), "input_mapping": {}, "output_mapping": {}})
        items = cf.list_subworkflows(run_id)
        assert len(items) == 2

    def test_recursive_revision_is_rejected(self, cf, run_id, factory):
        with factory() as session:
            parent = session.get(WorkflowRunModel, run_id)
            assert parent is not None
            parent_revision_id = parent.workflow_revision_id
        with pytest.raises(ValidationError_):
            cf.create_subworkflow(run_id, node_instance_id="recursive", parent_node_instance_id="p", config={"workflow_revision_id": str(parent_revision_id), "input_mapping": {}, "output_mapping": {}})
