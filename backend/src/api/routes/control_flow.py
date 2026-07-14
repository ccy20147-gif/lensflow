"""TF-WF-007 control-flow APIs backed by durable PostgreSQL execution state."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import ConflictError, NotFoundError
from src.domain.workflow.control_flow_service import ControlFlowService
from src.infra.db.session import get_session_factory
from src.infra.db.models import (
    ConditionModel, ForEachRunModel, JoinModel, MapItemRunModel,
    SubworkflowModel, WorkflowRunModel,
)
from src.infra.db.identity_repository import get_session_store
from src.schemas.models import OwnerScope
from src.schemas.enums import (
    ConditionOperator,
    ForEachMode,
    JoinStrategy,
)

router = APIRouter(prefix="/api/v1/control-flow", tags=["control-flow"])

# Composition root: share the same session_factory as the rest of the app.
_session_factory = get_session_factory()
_cf = ControlFlowService(session_factory=_session_factory)
_sessions = get_session_store()


def _owner(authorization: str | None) -> OwnerScope:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    try:
        return OwnerScope(kind="user", id=_sessions.account_for_token(authorization.removeprefix("Bearer ")))
    except (ConflictError, NotFoundError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


def _require_run_owner(run_id: UUID, authorization: str | None) -> OwnerScope:
    owner = _owner(authorization)
    with _session_factory() as session:
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
    return owner


def _require_record_owner(model: type, record_id: UUID, authorization: str | None) -> OwnerScope:
    owner = _owner(authorization)
    with _session_factory() as session:
        record = session.get(model, record_id)
        run_id = getattr(record, "run_id", None) if record is not None else None
        run = session.get(WorkflowRunModel, run_id) if run_id is not None else None
        if run is None or run.owner_scope != owner.scoped_id:
            raise HTTPException(status_code=404, detail="Control-flow record not found")
    return owner


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConditionCreateRequest(BaseModel):
    run_id: UUID
    node_instance_id: str
    operator: ConditionOperator
    threshold: Any = None
    value_path: str | None = None
    expression: dict[str, Any] | None = None
    config: dict[str, Any] = {}


class ConditionEvaluateRequest(BaseModel):
    resolved_value: Any


class JoinCreateRequest(BaseModel):
    run_id: UUID
    node_instance_id: str
    strategy: JoinStrategy
    source_node_ids: list[str]
    config: dict[str, Any] = {}


class MapItemCreateRequest(BaseModel):
    run_id: UUID
    node_instance_id: str
    item_key: str
    item_value: dict[str, Any] = {}
    item_index: int | None = None


class MapItemCompleteRequest(BaseModel):
    result: dict[str, Any] | None = None


class MapItemFailRequest(BaseModel):
    error: str | None = None


class ForEachCreateRequest(BaseModel):
    run_id: UUID
    node_instance_id: str
    mode: ForEachMode = ForEachMode.SEQUENTIAL
    collection_ref: str | None = None
    item_count: int = 0
    config: dict[str, Any] = {}


class MapClaimRequest(BaseModel):
    limit: int | None = None


class SubworkflowCreateRequest(BaseModel):
    run_id: UUID
    node_instance_id: str
    parent_node_instance_id: str
    config: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


@router.post("/conditions", status_code=201)
async def create_condition(body: ConditionCreateRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a condition node with static validation."""
    try:
        _require_run_owner(body.run_id, authorization)
        condition = _cf.create_condition(
            body.run_id,
            node_instance_id=body.node_instance_id,
            operator=body.operator,
            threshold=body.threshold,
            value_path=body.value_path,
            expression=body.expression,
            config=body.config,
        )
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "condition_id": str(condition.condition_id),
        "run_id": str(condition.run_id),
        "node_instance_id": condition.node_instance_id,
        "operator": condition.operator.value,
        "status": condition.status,
    }


@router.get("/conditions/{condition_id}")
async def get_condition(condition_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Get a condition by ID."""
    try:
        _require_record_owner(ConditionModel, condition_id, authorization)
        c = _cf.get_condition(condition_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "condition_id": str(c.condition_id),
        "run_id": str(c.run_id),
        "node_instance_id": c.node_instance_id,
        "operator": c.operator.value,
        "threshold": c.threshold,
        "status": c.status,
        "result": c.result,
    }


@router.get("/runs/{run_id}/conditions")
async def list_conditions(run_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    """List all conditions for a run."""
    _require_run_owner(run_id, authorization)
    conditions = _cf.list_conditions(run_id)
    return [
        {
            "condition_id": str(c.condition_id),
            "node_instance_id": c.node_instance_id,
            "operator": c.operator.value,
            "status": c.status,
            "result": c.result,
        }
        for c in conditions
    ]


@router.post("/conditions/{condition_id}/evaluate")
async def evaluate_condition(
    condition_id: UUID, body: ConditionEvaluateRequest, authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Evaluate a condition against a resolved value and persist result."""
    try:
        _require_record_owner(ConditionModel, condition_id, authorization)
        result = _cf.evaluate_condition(
            condition_id, resolved_value=body.resolved_value,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"condition_id": str(condition_id), "result": result}


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------


@router.post("/joins", status_code=201)
async def create_join(body: JoinCreateRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a join node with static validation."""
    try:
        _require_run_owner(body.run_id, authorization)
        join = _cf.create_join(
            body.run_id,
            node_instance_id=body.node_instance_id,
            strategy=body.strategy,
            source_node_ids=body.source_node_ids,
            config=body.config,
        )
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "join_id": str(join.join_id),
        "run_id": str(join.run_id),
        "node_instance_id": join.node_instance_id,
        "strategy": join.strategy.value,
        "status": join.status,
    }


@router.get("/joins/{join_id}")
async def get_join(join_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Get a join by ID."""
    try:
        _require_record_owner(JoinModel, join_id, authorization)
        j = _cf.get_join(join_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "join_id": str(j.join_id),
        "run_id": str(j.run_id),
        "node_instance_id": j.node_instance_id,
        "strategy": j.strategy.value,
        "status": j.status,
        "result": j.result,
    }


@router.get("/runs/{run_id}/joins")
async def list_joins(run_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    """List all joins for a run."""
    _require_run_owner(run_id, authorization)
    joins = _cf.list_joins(run_id)
    return [
        {
            "join_id": str(j.join_id),
            "node_instance_id": j.node_instance_id,
            "strategy": j.strategy.value,
            "status": j.status,
        }
        for j in joins
    ]


@router.post("/joins/{join_id}/resolve")
async def resolve_join(
    join_id: UUID, body: MapItemCompleteRequest, authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Resolve a join with a result payload."""
    try:
        _require_record_owner(JoinModel, join_id, authorization)
        j = _cf.update_join_result(join_id, result=body.result)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "join_id": str(j.join_id),
        "status": j.status,
        "result": j.result,
    }


# ---------------------------------------------------------------------------
# MapItem runs
# ---------------------------------------------------------------------------


@router.post("/map-items", status_code=201)
async def create_map_item(body: MapItemCreateRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a MapItem run record."""
    try:
        _require_run_owner(body.run_id, authorization)
        item = _cf.create_map_item(
            body.run_id,
            node_instance_id=body.node_instance_id,
            item_key=body.item_key,
            item_value=body.item_value,
            item_index=body.item_index,
        )
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "map_item_id": str(item.map_item_id),
        "run_id": str(item.run_id),
        "node_instance_id": item.node_instance_id,
        "item_key": item.item_key,
        "status": item.status.value,
    }


@router.get("/map-items/{map_item_id}")
async def get_map_item(map_item_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Get a MapItem by ID."""
    try:
        _require_record_owner(MapItemRunModel, map_item_id, authorization)
        item = _cf.get_map_item(map_item_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "map_item_id": str(item.map_item_id),
        "run_id": str(item.run_id),
        "node_instance_id": item.node_instance_id,
        "item_key": item.item_key,
        "status": item.status.value,
        "result": item.result,
        "error": item.error,
    }


@router.get("/runs/{run_id}/map-items")
async def list_map_items(
    run_id: UUID, node_instance_id: str | None = None, authorization: str | None = Header(None)
) -> list[dict[str, Any]]:
    """List all MapItems for a run, optionally filtered by node."""
    _require_run_owner(run_id, authorization)
    items = _cf.list_map_items(run_id, node_instance_id=node_instance_id)
    return [
        {
            "map_item_id": str(item.map_item_id),
            "node_instance_id": item.node_instance_id,
            "item_key": item.item_key,
            "status": item.status.value,
        }
        for item in items
    ]


@router.post("/map-items/{map_item_id}/start")
async def start_map_item(map_item_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Start a MapItem (PENDING → RUNNING)."""
    try:
        _require_record_owner(MapItemRunModel, map_item_id, authorization)
        item = _cf.start_map_item(map_item_id)
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"map_item_id": str(item.map_item_id), "status": item.status.value}


@router.post("/map-items/{map_item_id}/complete")
async def complete_map_item(
    map_item_id: UUID, body: MapItemCompleteRequest, authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Complete a MapItem (RUNNING → COMPLETED)."""
    try:
        _require_record_owner(MapItemRunModel, map_item_id, authorization)
        item = _cf.complete_map_item(map_item_id, result=body.result)
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"map_item_id": str(item.map_item_id), "status": item.status.value}


@router.post("/map-items/{map_item_id}/fail")
async def fail_map_item(
    map_item_id: UUID, body: MapItemFailRequest, authorization: str | None = Header(None)
) -> dict[str, Any]:
    """Fail a MapItem (RUNNING → FAILED)."""
    try:
        _require_record_owner(MapItemRunModel, map_item_id, authorization)
        item = _cf.fail_map_item(map_item_id, error=body.error)
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"map_item_id": str(item.map_item_id), "status": item.status.value}


@router.post("/map-items/{map_item_id}/skip")
async def skip_map_item(map_item_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Skip a MapItem (PENDING/RUNNING → SKIPPED)."""
    try:
        _require_record_owner(MapItemRunModel, map_item_id, authorization)
        item = _cf.skip_map_item(map_item_id)
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"map_item_id": str(item.map_item_id), "status": item.status.value}


# ---------------------------------------------------------------------------
# Bounded Map / OrderedMap / Fold
# ---------------------------------------------------------------------------


@router.post("/for-each", status_code=201)
async def create_for_each(body: ForEachCreateRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Freeze and materialize a bounded Map or OrderedMap execution."""
    try:
        _require_run_owner(body.run_id, authorization)
        fe = _cf.create_for_each(
            body.run_id,
            node_instance_id=body.node_instance_id,
            mode=body.mode,
            collection_ref=body.collection_ref,
            item_count=body.item_count,
            config=body.config,
        )
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "for_each_id": str(fe.for_each_id),
        "run_id": str(fe.run_id),
        "node_instance_id": fe.node_instance_id,
        "mode": fe.mode.value,
        "status": fe.status,
    }


@router.post("/for-each/{for_each_id}/claim")
async def claim_map_items(for_each_id: UUID, body: MapClaimRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Lease a policy-bounded batch of Map items in input order."""
    try:
        _require_record_owner(ForEachRunModel, for_each_id, authorization)
        items = _cf.claim_map_items(for_each_id, limit=body.limit)
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"items": [{"map_item_id": str(item.map_item_id), "item_index": item.item_index,
                       "item_value": item.item_value} for item in items]}


@router.get("/for-each/{for_each_id}/output")
async def ordered_map_output(for_each_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Expose aggregate output in frozen input order, not completion order."""
    try:
        _require_record_owner(ForEachRunModel, for_each_id, authorization)
        return {"output": _cf.ordered_map_output(for_each_id)}
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/for-each/{for_each_id}")
async def get_for_each(for_each_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Get a ForEach run by ID."""
    try:
        _require_record_owner(ForEachRunModel, for_each_id, authorization)
        fe = _cf.get_for_each(for_each_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "for_each_id": str(fe.for_each_id),
        "run_id": str(fe.run_id),
        "node_instance_id": fe.node_instance_id,
        "mode": fe.mode.value,
        "item_count": fe.item_count,
        "completed_count": fe.completed_count,
        "status": fe.status,
    }


@router.get("/runs/{run_id}/for-each")
async def list_for_each(run_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    """List all ForEach runs for a run."""
    _require_run_owner(run_id, authorization)
    items = _cf.list_for_each(run_id)
    return [
        {
            "for_each_id": str(fe.for_each_id),
            "node_instance_id": fe.node_instance_id,
            "mode": fe.mode.value,
            "status": fe.status,
        }
        for fe in items
    ]


# ---------------------------------------------------------------------------
# Fixed-revision SubworkflowCall
# ---------------------------------------------------------------------------


@router.post("/subworkflows", status_code=201)
async def create_subworkflow(body: SubworkflowCreateRequest, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Create a fixed-revision bounded child run."""
    try:
        _require_run_owner(body.run_id, authorization)
        sw = _cf.create_subworkflow(
            body.run_id,
            node_instance_id=body.node_instance_id,
            parent_node_instance_id=body.parent_node_instance_id,
            config=body.config,
        )
    except (ConflictError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "subworkflow_id": str(sw.subworkflow_id),
        "run_id": str(sw.run_id),
        "node_instance_id": sw.node_instance_id,
        "child_run_id": str(sw.child_run_id),
        "status": sw.status,
    }


@router.get("/subworkflows/{subworkflow_id}")
async def get_subworkflow(subworkflow_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Get a Subworkflow by ID."""
    try:
        _require_record_owner(SubworkflowModel, subworkflow_id, authorization)
        sw = _cf.get_subworkflow(subworkflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {
        "subworkflow_id": str(sw.subworkflow_id),
        "run_id": str(sw.run_id),
        "node_instance_id": sw.node_instance_id,
        "child_run_id": str(sw.child_run_id) if sw.child_run_id else None,
        "status": sw.status,
    }


@router.get("/runs/{run_id}/subworkflows")
async def list_subworkflows(run_id: UUID, authorization: str | None = Header(None)) -> list[dict[str, Any]]:
    """List all Subworkflows for a run."""
    _require_run_owner(run_id, authorization)
    items = _cf.list_subworkflows(run_id)
    return [
        {
            "subworkflow_id": str(sw.subworkflow_id),
            "node_instance_id": sw.node_instance_id,
            "child_run_id": str(sw.child_run_id) if sw.child_run_id else None,
            "status": sw.status,
        }
        for sw in items
    ]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/state")
async def run_control_state(run_id: UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Return the full control flow snapshot for a run.

    Aggregates conditions, joins, map-items, for-each runs, and
    subworkflows into a single payload so the Workbench UI can render
    the entire state with a single API call.
    """
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            c.name: (
                str(getattr(row, c.name))
                if c.name.endswith("_id")
                else getattr(row, c.name)
            )
            for c in row.__table__.columns
        }

    with _session_factory() as session:
        from src.infra.db.models import (
            ConditionModel, JoinModel, MapItemRunModel,
            ForEachRunModel, SubworkflowModel,
        )
        from sqlalchemy import select
        from src.infra.db.models import WorkflowRunModel
        run = session.get(WorkflowRunModel, run_id)
        if run is None or run.owner_scope != _owner(authorization).scoped_id:
            raise HTTPException(status_code=404, detail="WorkflowRun not found")
        conditions = session.scalars(
            select(ConditionModel).where(ConditionModel.run_id == run_id)
        ).all()
        joins = session.scalars(
            select(JoinModel).where(JoinModel.run_id == run_id)
        ).all()
        map_items = session.scalars(
            select(MapItemRunModel).where(MapItemRunModel.run_id == run_id)
        ).all()
        for_each_runs = session.scalars(
            select(ForEachRunModel).where(ForEachRunModel.run_id == run_id)
        ).all()
        subworkflows = session.scalars(
            select(SubworkflowModel).where(SubworkflowModel.run_id == run_id)
        ).all()

    return {
        "run_id": str(run_id),
        "conditions": [_row_to_dict(c) for c in conditions],
        "joins": [_row_to_dict(j) for j in joins],
        "map_items": [_row_to_dict(m) for m in map_items],
        "for_each_runs": [_row_to_dict(f) for f in for_each_runs],
        "subworkflows": [_row_to_dict(s) for s in subworkflows],
    }


@router.get("/health")
async def control_flow_health() -> dict[str, Any]:
    """Confirm the control flow service can reach PostgreSQL."""
    try:
        with _session_factory() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "PG_UNREACHABLE", "message": str(exc)}},
        )
