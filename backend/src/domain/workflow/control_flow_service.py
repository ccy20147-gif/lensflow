"""ToonFlow Backend — bounded durable control-flow execution.

All state lives in PostgreSQL.  Provides:
- Condition / Join CRUD with static config validation
- MapItem run-state transitions (PENDING → RUNNING → COMPLETED/FAILED/SKIPPED)
- bounded Map / OrderedMap execution and Fold checkpoints
- fixed-revision, non-recursive Subworkflow child runs
- Evaluator interface for condition evaluation
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ConditionModel,
    ForEachRunModel,
    JoinModel,
    MapItemRunModel,
    SubworkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
    NodeRunModel,
)
from src.schemas.enums import (
    ConditionOperator,
    ForEachMode,
    JoinStrategy,
    MapItemStatus,
)


# ---------------------------------------------------------------------------
# Evaluator interface (strategy pattern)
# ---------------------------------------------------------------------------


class ConditionEvaluator(ABC):
    """Abstract interface for runtime condition evaluation.

    Implementations compare a resolved value against the condition's
    threshold using the configured operator.
    """

    @abstractmethod
    def evaluate(
        self,
        *,
        operator: ConditionOperator,
        resolved_value: Any,
        threshold: Any,
    ) -> bool:
        """Evaluate a single condition and return True/False."""
        ...


class DefaultConditionEvaluator(ConditionEvaluator):
    """Default evaluator supporting all ConditionOperator values."""

    def evaluate(
        self,
        *,
        operator: ConditionOperator,
        resolved_value: Any,
        threshold: Any,
    ) -> bool:
        if operator == ConditionOperator.EQ:
            return resolved_value == threshold
        elif operator == ConditionOperator.NEQ:
            return resolved_value != threshold
        elif operator == ConditionOperator.GT:
            return resolved_value > threshold
        elif operator == ConditionOperator.GTE:
            return resolved_value >= threshold
        elif operator == ConditionOperator.LT:
            return resolved_value < threshold
        elif operator == ConditionOperator.LTE:
            return resolved_value <= threshold
        elif operator == ConditionOperator.IN_OP:
            if not isinstance(threshold, list):
                return False
            return resolved_value in threshold
        elif operator == ConditionOperator.CONTAINS:
            if not isinstance(resolved_value, (str, list)):
                return False
            return threshold in resolved_value
        elif operator == ConditionOperator.EXISTS:
            return resolved_value is not None
        raise ValueError(f"Unknown ConditionOperator: {operator}")


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------


def validate_condition_config(
    *,
    operator: ConditionOperator,
    threshold: Any = None,
    value_path: str | None = None,
    expression: dict[str, Any] | None = None,
) -> None:
    """Validate a condition node's static configuration.

    Raises ValidationError_ on invalid config.
    """
    valid_ops = set(ConditionOperator)
    if operator not in valid_ops:
        raise ValidationError_(
            message=f"Invalid ConditionOperator: {operator}",
            details={"valid": [v.value for v in valid_ops]},
        )
    # Comparison operators require a threshold
    if operator in (ConditionOperator.GT, ConditionOperator.GTE,
                    ConditionOperator.LT, ConditionOperator.LTE,
                    ConditionOperator.EQ, ConditionOperator.NEQ,
                    ConditionOperator.IN_OP, ConditionOperator.CONTAINS):
        if threshold is None:
            raise ValidationError_(
                message=f"Condition operator '{operator.value}' requires a threshold",
            )
    if operator == ConditionOperator.IN_OP and not isinstance(threshold, list):
        raise ValidationError_(
            message="Condition operator 'in' requires a list threshold",
        )
    if operator == ConditionOperator.EXISTS:
        if threshold is not None:
            raise ValidationError_(
                message="Condition operator 'exists' does not use a threshold",
            )


def validate_join_config(
    *,
    strategy: JoinStrategy,
    source_node_ids: list[str],
) -> None:
    """Validate a join node's static configuration.

    Raises ValidationError_ on invalid config.
    """
    valid_strategies = set(JoinStrategy)
    if strategy not in valid_strategies:
        raise ValidationError_(
            message=f"Invalid JoinStrategy: {strategy}",
            details={"valid": [v.value for v in valid_strategies]},
        )
    if not source_node_ids or len(source_node_ids) < 2:
        raise ValidationError_(
            message="Join requires at least 2 source node IDs",
        )


def validate_map_item_config(
    *,
    item_key: str | None = None,
    item_value: Any = None,
) -> None:
    """Validate a MapItem's static configuration."""
    if not item_key:
        raise ValidationError_(message="MapItem requires a non-empty item_key")


def validate_for_each_config(
    *, mode: ForEachMode, item_count: int, config: dict[str, Any]
) -> None:
    """Validate immutable, bounded Map/OrderedMap policy.

    The plan may choose lower limits but callers cannot increase them after a
    run exists.  ``ordered`` is represented by sequential mode or ``fold``.
    """
    if item_count < 0:
        raise ValidationError_(message="Map item_count cannot be negative")
    max_items = int(config.get("max_items", 50))
    concurrency = int(config.get("max_concurrency", 1 if mode == ForEachMode.SEQUENTIAL else 4))
    failure_policy = config.get("failure_policy", "fail_fast")
    if max_items < 1 or item_count > max_items:
        raise ValidationError_(message="Map item_count exceeds immutable max_items")
    if concurrency < 1 or concurrency > max_items:
        raise ValidationError_(message="Map max_concurrency is outside plan bounds")
    if mode == ForEachMode.SEQUENTIAL and concurrency != 1:
        raise ValidationError_(message="OrderedMap/Fold requires max_concurrency=1")
    if failure_policy not in {"fail_fast", "collect_errors", "configured_fallback"}:
        raise ValidationError_(message="Unsupported Map failure_policy")
    if config.get("fold", False) and mode != ForEachMode.SEQUENTIAL:
        raise ValidationError_(message="Fold requires ordered sequential execution")


def _as_uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError_(message=f"{field} must be a UUID") from exc


# ---------------------------------------------------------------------------
# ControlFlowService
# ---------------------------------------------------------------------------


class ControlFlowService:
    """Persistent control flow state management.

    All CRUD operations go through PostgreSQL via the injected
    ``session_factory``.  No in-memory fallback — this service is
    PG-only by design.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._evaluator: ConditionEvaluator = DefaultConditionEvaluator()

    def set_evaluator(self, evaluator: ConditionEvaluator) -> None:
        """Swap the condition evaluator (for testing)."""
        self._evaluator = evaluator

    # ------------------------------------------------------------------
    # Conditions
    # ------------------------------------------------------------------

    def create_condition(
        self,
        run_id: uuid.UUID,
        *,
        node_instance_id: str,
        operator: ConditionOperator,
        threshold: Any = None,
        value_path: str | None = None,
        expression: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> ConditionModel:
        """Create a condition node with static validation."""
        validate_condition_config(
            operator=operator, threshold=threshold,
            value_path=value_path, expression=expression,
        )
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(ConditionModel).where(
                    ConditionModel.run_id == run_id,
                    ConditionModel.node_instance_id == node_instance_id,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"Condition already exists for run {run_id} node {node_instance_id}",
                )
            row = ConditionModel(
                condition_id=uuid.uuid4(),
                run_id=run_id,
                node_instance_id=node_instance_id,
                operator=operator,
                threshold=threshold,
                value_path=value_path,
                expression=expression,
                status="pending",
                result=None,
                config=config or {},
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    def get_condition(self, condition_id: uuid.UUID) -> ConditionModel:
        with self._session_factory() as session:
            row = session.get(ConditionModel, condition_id)
            if row is None:
                raise NotFoundError("Condition", str(condition_id))
            return row

    def list_conditions(self, run_id: uuid.UUID) -> list[ConditionModel]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ConditionModel)
                .where(ConditionModel.run_id == run_id)
                .order_by(ConditionModel.created_at)
            ).all()
            return list(rows)

    def update_condition_result(
        self,
        condition_id: uuid.UUID,
        *,
        result: bool,
    ) -> ConditionModel:
        """Record a condition evaluation result."""
        with self._session_factory.begin() as session:
            row = session.get(ConditionModel, condition_id)
            if row is None:
                raise NotFoundError("Condition", str(condition_id))
            row.result = result
            row.status = "evaluated"
            session.flush()
            return row

    def evaluate_condition(
        self,
        condition_id: uuid.UUID,
        *,
        resolved_value: Any,
    ) -> bool:
        """Evaluate a condition against a resolved value and persist the result."""
        condition = self.get_condition(condition_id)
        result = self._evaluator.evaluate(
            operator=condition.operator,
            resolved_value=resolved_value,
            threshold=condition.threshold,
        )
        self.update_condition_result(condition_id=condition_id, result=result)
        return result

    # ------------------------------------------------------------------
    # Joins
    # ------------------------------------------------------------------

    def create_join(
        self,
        run_id: uuid.UUID,
        *,
        node_instance_id: str,
        strategy: JoinStrategy,
        source_node_ids: list[str],
        config: dict[str, Any] | None = None,
    ) -> JoinModel:
        """Create a join node with static validation."""
        validate_join_config(strategy=strategy, source_node_ids=source_node_ids)
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(JoinModel).where(
                    JoinModel.run_id == run_id,
                    JoinModel.node_instance_id == node_instance_id,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"Join already exists for run {run_id} node {node_instance_id}",
                )
            row = JoinModel(
                join_id=uuid.uuid4(),
                run_id=run_id,
                node_instance_id=node_instance_id,
                strategy=strategy,
                source_node_ids=source_node_ids,
                status="pending",
                result=None,
                config=config or {},
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    def get_join(self, join_id: uuid.UUID) -> JoinModel:
        with self._session_factory() as session:
            row = session.get(JoinModel, join_id)
            if row is None:
                raise NotFoundError("Join", str(join_id))
            return row

    def list_joins(self, run_id: uuid.UUID) -> list[JoinModel]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(JoinModel)
                .where(JoinModel.run_id == run_id)
                .order_by(JoinModel.created_at)
            ).all()
            return list(rows)

    def update_join_result(
        self,
        join_id: uuid.UUID,
        *,
        result: dict[str, Any] | None = None,
    ) -> JoinModel:
        """Record a join resolution result."""
        with self._session_factory.begin() as session:
            row = session.get(JoinModel, join_id)
            if row is None:
                raise NotFoundError("Join", str(join_id))
            row.result = result
            row.status = "completed"
            session.flush()
            return row

    # ------------------------------------------------------------------
    # MapItem runs (per-item ForEach state)
    # ------------------------------------------------------------------

    def create_map_item(
        self,
        run_id: uuid.UUID,
        *,
        node_instance_id: str,
        item_key: str,
        item_value: dict[str, Any] | None = None,
        item_index: int | None = None,
    ) -> MapItemRunModel:
        """Create a MapItem run record."""
        validate_map_item_config(item_key=item_key, item_value=item_value)
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(MapItemRunModel).where(
                    MapItemRunModel.run_id == run_id,
                    MapItemRunModel.node_instance_id == node_instance_id,
                    MapItemRunModel.item_key == item_key,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"MapItem {item_key} already exists for run {run_id} node {node_instance_id}",
                )
            row = MapItemRunModel(
                map_item_id=uuid.uuid4(),
                run_id=run_id,
                node_instance_id=node_instance_id,
                item_key=item_key,
                item_index=item_index if item_index is not None else 0,
                item_value=item_value or {},
                status=MapItemStatus.PENDING,
                result=None,
                error=None,
                started_at=None,
                completed_at=None,
            )
            session.add(row)
            session.flush()
            return row

    def get_map_item(self, map_item_id: uuid.UUID) -> MapItemRunModel:
        with self._session_factory() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            return row

    def list_map_items(
        self,
        run_id: uuid.UUID,
        node_instance_id: str | None = None,
    ) -> list[MapItemRunModel]:
        with self._session_factory() as session:
            query: Select = select(MapItemRunModel).where(MapItemRunModel.run_id == run_id)
            if node_instance_id is not None:
                query = query.where(MapItemRunModel.node_instance_id == node_instance_id)
            query = query.order_by(MapItemRunModel.item_index, MapItemRunModel.item_key)
            rows = session.scalars(query).all()
            return list(rows)

    def start_map_item(self, map_item_id: uuid.UUID) -> MapItemRunModel:
        """Transition MapItem from PENDING to RUNNING."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            if row.status != MapItemStatus.PENDING:
                raise ConflictError(
                    f"MapItem {map_item_id} status is {row.status}, cannot start",
                )
            row.status = MapItemStatus.RUNNING
            row.started_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def _complete_map_item_legacy(
        self,
        map_item_id: uuid.UUID,
        *,
        result: dict[str, Any] | None = None,
    ) -> MapItemRunModel:
        """Transition MapItem from RUNNING to COMPLETED."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            if row.status != MapItemStatus.RUNNING:
                raise ConflictError(
                    f"MapItem {map_item_id} status is {row.status}, cannot complete",
                )
            row.status = MapItemStatus.COMPLETED
            row.result = result
            row.completed_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def _fail_map_item_legacy(
        self,
        map_item_id: uuid.UUID,
        *,
        error: str | None = None,
    ) -> MapItemRunModel:
        """Transition MapItem from RUNNING to FAILED."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            if row.status != MapItemStatus.RUNNING:
                raise ConflictError(
                    f"MapItem {map_item_id} status is {row.status}, cannot fail",
                )
            row.status = MapItemStatus.FAILED
            row.error = error
            row.completed_at = datetime.now(timezone.utc)
            session.flush()
            return row

    def skip_map_item(self, map_item_id: uuid.UUID) -> MapItemRunModel:
        """Skip a MapItem (PENDING → SKIPPED)."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            # Allow skipping from PENDING or RUNNING
            if row.status not in (MapItemStatus.PENDING, MapItemStatus.RUNNING):
                raise ConflictError(
                    f"MapItem {map_item_id} status is {row.status}, cannot skip",
                )
            row.status = MapItemStatus.SKIPPED
            row.completed_at = datetime.now(timezone.utc)
            session.flush()
            return row

    # ------------------------------------------------------------------
    # Bounded Map / OrderedMap / Fold
    # ------------------------------------------------------------------

    def create_for_each(
        self,
        run_id: uuid.UUID,
        *,
        node_instance_id: str,
        mode: ForEachMode = ForEachMode.SEQUENTIAL,
        collection_ref: str | None = None,
        item_count: int = 0,
        config: dict[str, Any] | None = None,
    ) -> ForEachRunModel:
        """Create an immutable bounded Map execution and its item records.

        Callers may pass ``config.items`` as a frozen collection.  The older
        ``item_count`` API remains supported for callers which materialize
        items separately, but execution cannot start until all indexes exist.
        """
        frozen_config = dict(config or {})
        items = frozen_config.pop("items", None)
        if items is not None:
            if not isinstance(items, list):
                raise ValidationError_(message="Map items must be a list")
            item_count = len(items)
        validate_for_each_config(mode=mode, item_count=item_count, config=frozen_config)
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(ForEachRunModel).where(
                    ForEachRunModel.run_id == run_id,
                    ForEachRunModel.node_instance_id == node_instance_id,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"ForEach already exists for run {run_id} node {node_instance_id}",
                )
            row = ForEachRunModel(
                for_each_id=uuid.uuid4(),
                run_id=run_id,
                node_instance_id=node_instance_id,
                mode=mode,
                collection_ref=collection_ref,
                item_count=item_count,
                completed_count=0,
                failed_count=0,
                status="pending",
                config=frozen_config,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            if items is not None:
                for index, value in enumerate(items):
                    session.add(MapItemRunModel(
                        map_item_id=uuid.uuid4(), run_id=run_id,
                        node_instance_id=node_instance_id, item_key=str(index),
                        item_index=index,
                        item_value=value if isinstance(value, dict) else {"value": value},
                        status=MapItemStatus.PENDING,
                    ))
            session.flush()
            return row

    def get_for_each(self, for_each_id: uuid.UUID) -> ForEachRunModel:
        with self._session_factory() as session:
            row = session.get(ForEachRunModel, for_each_id)
            if row is None:
                raise NotFoundError("ForEach", str(for_each_id))
            return row

    def list_for_each(self, run_id: uuid.UUID) -> list[ForEachRunModel]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ForEachRunModel)
                .where(ForEachRunModel.run_id == run_id)
                .order_by(ForEachRunModel.created_at)
            ).all()
            return list(rows)

    def claim_map_items(self, for_each_id: uuid.UUID, *, limit: int | None = None) -> list[MapItemRunModel]:
        """Atomically claim the next bounded batch in deterministic order.

        PostgreSQL row locking prevents two workers from exceeding the frozen
        concurrency limit.  Sequential/Fold mode permits exactly one item.
        """
        with self._session_factory.begin() as session:
            # The parent flow row is the concurrency reservation.  Lock it
            # before counting RUNNING items so concurrent workers cannot each
            # observe spare capacity and claim disjoint SKIP LOCKED rows.
            flow = session.get(ForEachRunModel, for_each_id, with_for_update=True)
            if flow is None:
                raise NotFoundError("ForEach", str(for_each_id))
            cfg = dict(flow.config or {})
            max_concurrency = int(cfg.get("max_concurrency", 1 if flow.mode == ForEachMode.SEQUENTIAL else 4))
            requested = max_concurrency if limit is None else limit
            if requested < 1:
                raise ValidationError_(message="Map claim limit must be positive")
            running = session.scalar(select(func.count()).select_from(MapItemRunModel).where(
                MapItemRunModel.run_id == flow.run_id,
                MapItemRunModel.node_instance_id == flow.node_instance_id,
                MapItemRunModel.status == MapItemStatus.RUNNING,
            )) or 0
            capacity = max(0, min(requested, max_concurrency - running))
            if flow.mode == ForEachMode.SEQUENTIAL:
                capacity = min(capacity, 1)
            if capacity == 0:
                return []
            stmt = (select(MapItemRunModel).where(
                MapItemRunModel.run_id == flow.run_id,
                MapItemRunModel.node_instance_id == flow.node_instance_id,
                MapItemRunModel.status == MapItemStatus.PENDING,
            ).order_by(MapItemRunModel.item_index).with_for_update(skip_locked=True).limit(capacity))
            claimed = list(session.scalars(stmt))
            now = datetime.now(timezone.utc)
            for item in claimed:
                item.status = MapItemStatus.RUNNING
                item.started_at = now
            if claimed:
                flow.status = "running"
            session.flush()
            return claimed

    def complete_map_item(
        self, map_item_id: uuid.UUID, *, result: dict[str, Any] | None = None
    ) -> MapItemRunModel:
        """Complete an item and atomically advance aggregate/Fold checkpoint."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            if row.status != MapItemStatus.RUNNING:
                raise ConflictError(f"MapItem {map_item_id} is not running")
            row.status, row.result, row.completed_at = MapItemStatus.COMPLETED, result, datetime.now(timezone.utc)
            flow = session.scalar(select(ForEachRunModel).where(
                ForEachRunModel.run_id == row.run_id,
                ForEachRunModel.node_instance_id == row.node_instance_id,
            ))
            if flow is not None:
                flow.completed_count += 1
                cfg = dict(flow.config or {})
                if cfg.get("fold", False):
                    expected = int(cfg.get("checkpoint_index", -1)) + 1
                    if row.item_index != expected:
                        raise ConflictError("Fold result is out of order; checkpoint is unchanged")
                    accumulator = cfg.get("accumulator", [])
                    if not isinstance(accumulator, list):
                        raise ConflictError("Fold accumulator checkpoint is corrupt")
                    accumulator.append(result)
                    cfg["accumulator"] = accumulator
                    cfg["checkpoint_index"] = row.item_index
                    flow.config = cfg
                total = session.scalar(select(func.count()).select_from(MapItemRunModel).where(
                    MapItemRunModel.run_id == row.run_id, MapItemRunModel.node_instance_id == row.node_instance_id,
                )) or 0
                if flow.completed_count + flow.failed_count >= total:
                    flow.status = "completed" if not flow.failed_count else "failed"
            session.flush()
            return row

    def fail_map_item(self, map_item_id: uuid.UUID, *, error: str | None = None) -> MapItemRunModel:
        """Apply the frozen failure policy and never mutate completed output."""
        with self._session_factory.begin() as session:
            row = session.get(MapItemRunModel, map_item_id)
            if row is None:
                raise NotFoundError("MapItem", str(map_item_id))
            if row.status != MapItemStatus.RUNNING:
                raise ConflictError(f"MapItem {map_item_id} is not running")
            row.status, row.error, row.completed_at = MapItemStatus.FAILED, error, datetime.now(timezone.utc)
            flow = session.scalar(select(ForEachRunModel).where(
                ForEachRunModel.run_id == row.run_id, ForEachRunModel.node_instance_id == row.node_instance_id,
            ))
            if flow is not None:
                flow.failed_count += 1
                policy = (flow.config or {}).get("failure_policy", "fail_fast")
                if policy == "fail_fast":
                    flow.status = "failed"
                    for pending in session.scalars(select(MapItemRunModel).where(
                        MapItemRunModel.run_id == row.run_id, MapItemRunModel.node_instance_id == row.node_instance_id,
                        MapItemRunModel.status == MapItemStatus.PENDING,
                    )):
                        pending.status, pending.completed_at = MapItemStatus.SKIPPED, datetime.now(timezone.utc)
                elif policy == "configured_fallback" and not (flow.config or {}).get("fallback_node_id"):
                    raise ConflictError("configured_fallback requires fallback_node_id")
            session.flush()
            return row

    def ordered_map_output(self, for_each_id: uuid.UUID) -> list[dict[str, Any] | None]:
        """Return finished Map output by input index, never completion order."""
        with self._session_factory() as session:
            flow = session.get(ForEachRunModel, for_each_id)
            if flow is None:
                raise NotFoundError("ForEach", str(for_each_id))
            rows = session.scalars(select(MapItemRunModel).where(
                MapItemRunModel.run_id == flow.run_id, MapItemRunModel.node_instance_id == flow.node_instance_id,
            ).order_by(MapItemRunModel.item_index)).all()
            return [row.result if row.status == MapItemStatus.COMPLETED else None for row in rows]

    # ------------------------------------------------------------------
    # Fixed-revision SubworkflowCall
    # ------------------------------------------------------------------

    def create_subworkflow(
        self,
        run_id: uuid.UUID,
        *,
        node_instance_id: str,
        parent_node_instance_id: str,
        config: dict[str, Any] | None = None,
    ) -> SubworkflowModel:
        """Create a bounded child WorkflowRun pinned to a revision.

        ``config`` must carry a fixed ``workflow_revision_id`` plus explicit
        JSON-object input/output mappings.  A child never inherits broader
        permissions: it retains the parent owner scope and frozen inputs.
        """
        cfg = dict(config or {})
        child_revision_id = _as_uuid(cfg.get("workflow_revision_id"), "workflow_revision_id")
        depth = int(cfg.get("depth", 1))
        max_depth = int(cfg.get("max_depth", 3))
        if depth < 1 or depth > max_depth:
            raise ValidationError_(message="Subworkflow depth exceeds fixed plan limit")
        if not isinstance(cfg.get("input_mapping", {}), dict) or not isinstance(cfg.get("output_mapping", {}), dict):
            raise ValidationError_(message="Subworkflow requires typed input_mapping and output_mapping")
        with self._session_factory.begin() as session:
            existing = session.scalar(
                select(SubworkflowModel).where(
                    SubworkflowModel.run_id == run_id,
                    SubworkflowModel.node_instance_id == node_instance_id,
                )
            )
            if existing is not None:
                raise ConflictError(
                    f"Subworkflow already exists for run {run_id} node {node_instance_id}",
                )
            parent = session.get(WorkflowRunModel, run_id)
            if parent is None:
                raise NotFoundError("WorkflowRun", str(run_id))
            child_revision = session.get(WorkflowRevisionModel, child_revision_id)
            if child_revision is None:
                raise NotFoundError("WorkflowRevision", str(child_revision_id))
            if child_revision_id == parent.workflow_revision_id:
                raise ValidationError_(message="Recursive SubworkflowCall is forbidden")
            child = WorkflowRunModel(
                run_id=uuid.uuid4(), workflow_revision_id=child_revision_id,
                compiled_plan_id=uuid.uuid4(), owner_scope=parent.owner_scope,
                input_snapshot=dict(cfg.get("input_mapping", {})), status="queued",
                created_at=datetime.now(timezone.utc),
            )
            session.add(child)
            session.flush()
            graph = child_revision.graph or {}
            nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
            max_nodes = int(cfg.get("max_child_nodes", 50))
            if len(nodes) > max_nodes:
                raise ValidationError_(message="Subworkflow exceeds max_child_nodes")
            for node in nodes:
                if isinstance(node, dict):
                    session.add(NodeRunModel(node_run_id=uuid.uuid4(), run_id=child.run_id,
                        node_instance_id=str(node.get("id", "unknown")), node_type_id=str(node.get("type", "unknown")), status="pending"))
            row = SubworkflowModel(
                subworkflow_id=uuid.uuid4(),
                run_id=run_id,
                node_instance_id=node_instance_id,
                child_run_id=child.run_id,
                parent_node_instance_id=parent_node_instance_id,
                status="pending",
                config=cfg,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

    def propagate_subworkflow_status(self, subworkflow_id: uuid.UUID, *, parent_cancelled: bool = False) -> SubworkflowModel:
        """Synchronize child terminal/cancellation state to the parent binding."""
        with self._session_factory.begin() as session:
            binding = session.get(SubworkflowModel, subworkflow_id)
            if binding is None:
                raise NotFoundError("Subworkflow", str(subworkflow_id))
            child = self._require_child(session, binding)
            if parent_cancelled:
                child.status, binding.status = "cancelled", "cancelled"
            elif child.status in {"completed", "failed", "cancelled"}:
                binding.status = child.status
            else:
                binding.status = "running"
            session.flush()
            return binding

    @staticmethod
    def _require_child(session: Any, binding: SubworkflowModel) -> WorkflowRunModel:
        if binding.child_run_id is None:
            raise ConflictError("Subworkflow binding has no child run")
        child = session.get(WorkflowRunModel, binding.child_run_id)
        if child is None:
            raise NotFoundError("WorkflowRun", str(binding.child_run_id))
        return child

    def get_subworkflow(self, subworkflow_id: uuid.UUID) -> SubworkflowModel:
        with self._session_factory() as session:
            row = session.get(SubworkflowModel, subworkflow_id)
            if row is None:
                raise NotFoundError("Subworkflow", str(subworkflow_id))
            return row

    def list_subworkflows(self, run_id: uuid.UUID) -> list[SubworkflowModel]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SubworkflowModel)
                .where(SubworkflowModel.run_id == run_id)
                .order_by(SubworkflowModel.created_at)
            ).all()
            return list(rows)
