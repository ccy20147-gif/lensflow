"""TF-ASR-001: Media Recipe API Routes — PostgreSQL-backed.

Endpoints for Media Recipe definitions, draft/revision lifecycle with CAS,
validation, and dry-run compilation.
"""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.core.exceptions import PolicyBlockedError
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown
from src.domain.runtime.runtime_service import RuntimeService
from src.infra.db.session import get_session_factory
from src.infra.db.models import (
    MediaRecipeDefinitionModel,
    MediaRecipeRevisionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    WorkflowRunModel,
)
from src.infra.db.recipe_repository import SqlMediaRecipeService
from src.schemas.models import MediaRecipeRevision
from src.api.auth import require_owner
from src.domain.recipe.recipe_runtime import RecipeRuntimeService

_recipe = SqlMediaRecipeService()
_runtime = RuntimeService(session_factory=get_session_factory())
_recipe_runtime = RecipeRuntimeService(get_session_factory())

router = APIRouter(prefix="/api/v1/recipes", tags=["recipes"])


def _require_recipe_owner(recipe_id: UUID, authorization: str | None) -> str:
    owner_scope = require_owner(authorization)[1].scoped_id
    definition = _recipe._repo.get_definition(recipe_id)
    if definition.owner_scope != owner_scope:
        raise ForbiddenError("MediaRecipe belongs to a different owner_scope")
    return owner_scope


# -- Request / Response models --


class CreateRecipeRequest(BaseModel):
    name: str
    description: str = ""
    recipe_type: str = ""


class UpdateRecipeRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    recipe_type: str | None = None


class CreateRevisionRequest(BaseModel):
    body: dict
    base_hash: str | None = None


class RecipeValidateRequest(BaseModel):
    body: dict


class RecipeDryRunRequest(BaseModel):
    body: dict


class RecipeExecuteRequest(BaseModel):
    node_run_attempt_id: UUID
    # Execution never accepts an unfrozen recipe body.  ``body`` remains an
    # optional compatibility field solely so a stale client receives a clear
    # conflict instead of silently running its draft.
    recipe_revision_id: UUID | None = None
    body: dict
    idempotency_key: str
    inputs: dict = {}


class DryRunResponse(BaseModel):
    valid: bool
    step_count: int
    plan_hash: str = ""
    control_outcomes: list[dict] = []


# -- Definition endpoints --


@router.post("")
async def create_recipe(body: CreateRecipeRequest, authorization: str | None = Header(None)) -> dict:
    """Create a new Media Recipe definition."""
    try:
        row = _recipe._repo.create_definition(
            name=body.name,
            description=body.description,
            owner_scope=require_owner(authorization)[1].scoped_id,
            recipe_type=body.recipe_type,
        )
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "recipe_id": str(row.recipe_id),
        "name": row.name,
        "description": row.description,
        "owner_scope": row.owner_scope,
        "recipe_type": row.recipe_type,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Get a Media Recipe definition by ID."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        row = _recipe._repo.get_definition(recipe_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "recipe_id": str(row.recipe_id),
        "name": row.name,
        "description": row.description,
        "owner_scope": row.owner_scope,
        "recipe_type": row.recipe_type,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("")
async def list_recipes(
    recipe_type: str | None = None, authorization: str | None = Header(None)
) -> list[dict]:
    """List all Media Recipe definitions, with optional filters."""
    rows = _recipe._repo.list_definitions(owner_scope=require_owner(authorization)[1].scoped_id, recipe_type=recipe_type)
    return [
        {
            "recipe_id": str(r.recipe_id),
            "name": r.name,
            "description": r.description,
            "owner_scope": r.owner_scope,
            "recipe_type": r.recipe_type,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
        }
        for r in rows
    ]


@router.get("/published")
async def list_published_recipes() -> dict:
    """List Media Recipe definitions with at least one active revision.

    The canvas palette uses this to surface runnable Media Recipes as node
    candidates. Provider availability is evaluated at request time and no
    credential is ever returned.
    """
    from src.infra.db.models import MediaRecipeDefinitionModel, MediaRecipeRevisionModel
    from sqlalchemy import select
    provider_configured = AtlasCloudAdapter().configured
    with _recipe._factory() as session:
        defs = session.scalars(select(MediaRecipeDefinitionModel)).all()
        out = []
        for d in defs:
            rev = session.scalar(
                select(MediaRecipeRevisionModel)
                .where(
                    MediaRecipeRevisionModel.recipe_id == d.recipe_id,
                    MediaRecipeRevisionModel.status == "active",
                )
                .order_by(MediaRecipeRevisionModel.revision_number.desc())
                .limit(1)
            )
            if rev is None:
                continue
            out.append({
                "recipe_id": str(d.recipe_id),
                "name": d.name,
                "description": d.description,
                "owner_scope": d.owner_scope,
                "recipe_type": d.recipe_type,
                "revision_id": str(rev.revision_id),
                "revision_number": rev.revision_number,
                "content_hash": rev.content_hash,
                "provider_configured": provider_configured,
            })
    return {"recipes": out, "count": len(out), "provider_configured": provider_configured}


@router.patch("/{recipe_id}")
async def update_recipe(recipe_id: UUID, body: UpdateRecipeRequest, authorization: str | None = Header(None)) -> dict:
    """Update a Media Recipe definition."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        row = _recipe._repo.update_definition(
            recipe_id,
            name=body.name,
            description=body.description,
            recipe_type=body.recipe_type,
        )
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {
        "recipe_id": str(row.recipe_id),
        "name": row.name,
        "description": row.description,
        "owner_scope": row.owner_scope,
        "recipe_type": row.recipe_type,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.delete("/{recipe_id}")
async def delete_recipe(recipe_id: UUID, authorization: str | None = Header(None)) -> dict:
    """Delete a Media Recipe and all its revisions."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        _recipe._repo.delete_definition(recipe_id)
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    return {"status": "deleted"}


# -- Revision endpoints --


@router.post("/{recipe_id}/revisions", response_model=MediaRecipeRevision)
async def create_recipe_revision(
    recipe_id: UUID, body: CreateRevisionRequest, authorization: str | None = Header(None)
) -> MediaRecipeRevision:
    """Create a new draft revision (with CAS)."""
    try:
        _recipe.validate(body.body)
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())
    try:
        _require_recipe_owner(recipe_id, authorization)
        return _recipe._repo.create_revision(recipe_id, body.body, base_hash=body.base_hash)
    except (ConflictError, NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/{recipe_id}/revisions", response_model=list[MediaRecipeRevision])
async def list_recipe_revisions(recipe_id: UUID, authorization: str | None = Header(None)) -> list[MediaRecipeRevision]:
    """List all revisions for a Media Recipe."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        return _recipe._repo.list_revisions(recipe_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


@router.get("/{recipe_id}/revisions/{revision_id}", response_model=MediaRecipeRevision)
async def get_recipe_revision(recipe_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> MediaRecipeRevision:
    """Get a specific Media Recipe revision."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        revision = _recipe._repo.get_revision(revision_id)
        if revision.recipe_id != recipe_id:
            raise NotFoundError("MediaRecipeRevision", str(revision_id))
        return revision
    except (NotFoundError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/{recipe_id}/revisions/{revision_id}/promote", response_model=MediaRecipeRevision
)
async def promote_recipe_revision(recipe_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> MediaRecipeRevision:
    """Promote a draft revision to active."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        revision = _recipe._repo.get_revision(revision_id)
        if revision.recipe_id != recipe_id:
            raise NotFoundError("MediaRecipeRevision", str(revision_id))
        return _recipe._repo.promote_revision(revision_id)
    except (NotFoundError, ConflictError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post(
    "/{recipe_id}/revisions/{revision_id}/retire", response_model=MediaRecipeRevision
)
async def retire_recipe_revision(recipe_id: UUID, revision_id: UUID, authorization: str | None = Header(None)) -> MediaRecipeRevision:
    """Retire an active revision."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        revision = _recipe._repo.get_revision(revision_id)
        if revision.recipe_id != recipe_id:
            raise NotFoundError("MediaRecipeRevision", str(revision_id))
        return _recipe._repo.retire_revision(revision_id)
    except (NotFoundError, ConflictError, ForbiddenError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/{recipe_id}/revisions/{revision_id}/diff/{other_revision_id}")
async def diff_recipe_revisions(
    recipe_id: UUID,
    revision_id: UUID,
    other_revision_id: UUID,
    authorization: str | None = Header(None),
) -> dict:
    """Compare two immutable revisions of one owner-scoped Media Recipe."""
    try:
        _require_recipe_owner(recipe_id, authorization)
        left = _recipe._repo.get_revision(revision_id)
        right = _recipe._repo.get_revision(other_revision_id)
        if left.recipe_id != recipe_id or right.recipe_id != recipe_id:
            raise NotFoundError("MediaRecipeRevision", str(revision_id))
        return _recipe._repo.diff_revisions(revision_id, other_revision_id)
    except (NotFoundError, ConflictError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc


# -- Validation / Dry-run endpoints --


@router.post("/validate")
async def validate_recipe_body(body: RecipeValidateRequest) -> dict:
    """Static validation of a Media Recipe body without persisting."""
    try:
        _recipe.validate(body.body)
        return {"valid": True}
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/dry-run")
async def dry_run_recipe(body: RecipeDryRunRequest) -> DryRunResponse:
    """Compile and return the frozen operator plan without network I/O."""
    result = _recipe.dry_run(body.body)
    return DryRunResponse(valid=result["valid"], step_count=result["step_count"], plan_hash=result["plan_hash"], control_outcomes=result["compiled_plan"]["control_outcomes"])


@router.post("/execute")
async def execute_recipe(body: RecipeExecuteRequest, authorization: str | None = Header(None)) -> dict:
    """Dispatch one frozen AtlasCloud operator through the durable runtime.

    A runtime worker expands a recipe plan to one node attempt per operator.
    This endpoint is the provider boundary used by that worker: it never calls
    the network before RuntimeService committed provider_dispatch.
    """
    try:
        request_owner = require_owner(authorization)[1].scoped_id
        adapter = AtlasCloudAdapter()
        # Reject deployment misconfiguration before materializing child
        # attempts or writing an outbox event. This is a policy boundary, not
        # a recoverable provider submission.
        if not adapter.configured:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        # Validate the actor, parent attempt and active immutable Recipe
        # revision before materialising child work, dispatching an outbox
        # event, or touching AtlasCloud.  A browser body is never executable
        # source of truth.
        with get_session_factory()() as session:
            parent = session.get(NodeRunAttemptModel, body.node_run_attempt_id)
            parent_node = session.get(NodeRunModel, parent.node_run_id) if parent else None
            run = session.get(WorkflowRunModel, parent_node.run_id) if parent_node else None
            if parent is None or parent_node is None or run is None:
                raise NotFoundError("Recipe parent NodeRunAttempt", str(body.node_run_attempt_id))
            if run.owner_scope != request_owner:
                raise ForbiddenError("Recipe execution attempt belongs to a different owner_scope")
            fixed = dict(parent.fixed_input or {})
            pinned_revision = fixed.get("recipe_revision_id")
            if not pinned_revision:
                raise ConflictError("Recipe parent attempt lacks a pinned MediaRecipeRevision")
            try:
                pinned_revision_id = UUID(str(pinned_revision))
            except (TypeError, ValueError) as exc:
                raise ConflictError("Recipe parent attempt has an invalid pinned MediaRecipeRevision") from exc
            if body.recipe_revision_id is not None and body.recipe_revision_id != pinned_revision_id:
                raise ConflictError("Recipe execution revision does not match the fixed parent attempt")
            revision = session.get(MediaRecipeRevisionModel, pinned_revision_id)
            if revision is None or revision.status != "active":
                raise ConflictError("Recipe execution requires an active frozen MediaRecipeRevision")
            definition = session.get(MediaRecipeDefinitionModel, revision.recipe_id)
            if definition is None or definition.owner_scope != request_owner:
                raise ForbiddenError("Pinned MediaRecipeRevision belongs to a different owner_scope")
            frozen_body = dict(revision.body or {})
            if not frozen_body.get("compiled_plan"):
                raise ConflictError("Pinned MediaRecipeRevision lacks a compiled immutable plan")
            # Inputs are fixed with the parent attempt.  A request may not
            # substitute fresh browser values at the provider boundary.
            fixed_inputs = dict(fixed.get("recipe_inputs") or fixed.get("root_inputs") or {})
        compiled = _recipe.dry_run(frozen_body)
        # A Recipe is a second-level DAG: materialize one durable child attempt
        # per frozen operator before any provider side effect. Worker execution
        # consumes those children in topological order.
        child_attempt_ids = _recipe_runtime.materialize(
            parent_attempt_id=body.node_run_attempt_id,
            body=frozen_body,
            inputs=fixed_inputs,
        )
        external = next((step for step in compiled["compiled_plan"]["steps"] if step["operator"] in {"atlas_llm", "atlas_image", "atlas_video"}), None)
        if external is None:
            raise ValidationError_("Recipe has no AtlasCloud operator to execute")
        model_id = external["model_id"]
        if not model_id:
            raise ValidationError_("AtlasCloud operator requires model_id")
        request = {"input": fixed_inputs, "parameters": external["parameters"]}
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        external_index = compiled["compiled_plan"]["steps"].index(external)
        provider_attempt, dispatch = _runtime.dispatch_provider(
            child_attempt_ids[external_index], provider_id="atlascloud", model_id=model_id,
            idempotency_key=body.idempotency_key, request_body_hash=request_hash,
        )
        operation = {"atlas_llm": "llm", "atlas_image": "image", "atlas_video": "video"}[external["operator"]]
        try:
            submission = adapter.submit(operation=operation, model_id=model_id, payload=request, idempotency_key=body.idempotency_key)
        except AtlasSubmissionUnknown:
            _runtime.mark_provider_unknown(provider_attempt.provider_attempt_id)
            return {"provider_attempt_id": str(provider_attempt.provider_attempt_id), "status": "unknown", "outbox_event_id": str(dispatch.event_id), "operator_attempt_ids": [str(value) for value in child_attempt_ids]}
        if submission.task_id:
            _runtime.bind_provider_task(provider_attempt.provider_attempt_id, submission.task_id)
        if not submission.outputs or not all(isinstance(output, dict) for output in submission.outputs):
            _runtime.fail_attempt(body.node_run_attempt_id)
            raise ValidationError_("Recipe provider output must contain typed object results")
        output_refs = frozen_body.get("public_output_schema_refs", [])
        schema_ref = output_refs[0] if isinstance(output_refs, list) and output_refs else "media_output.v1"
        schema_id, _, raw_version = str(schema_ref).rpartition(".v")
        if not schema_id or not raw_version.isdigit():
            _runtime.fail_attempt(body.node_run_attempt_id)
            raise ValidationError_("Recipe public output schema must use schema_id.vN")
        owner_scope = request_owner
        record, publish, artifact_ids = _runtime.publish_provider_json_outputs(
            provider_attempt.provider_attempt_id, owner_scope=owner_scope, schema_id=schema_id,
            schema_version=int(raw_version), outputs=submission.outputs, model_version=submission.model_version,
            response_fingerprint=submission.raw_fingerprint, usage=submission.usage, actual_cost=submission.actual_cost,
        )
        return {"provider_attempt_id": str(provider_attempt.provider_attempt_id), "status": "completed", "record_id": str(record.record_id),
            "artifact_version_ids": [str(value) for value in artifact_ids], "outbox_event_id": str(publish.event_id), "operator_attempt_ids": [str(value) for value in child_attempt_ids]}
    except (ValidationError_, PolicyBlockedError, ConflictError, NotFoundError, ForbiddenError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())
