"""TF-WF-002: Registry API Routes — PostgreSQL-backed.

Endpoints for node definition registration, activation, retirement,
querying, converter management, and registry snapshots.

All persistent state lives in PostgreSQL via SqlRegistryService.  The
in-memory ``RegistryService`` is preserved as a focused unit-test double
and is **not** wired into the API surface.
"""
from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import NotFoundError, ConflictError, ValidationError_
from src.core.config import settings
from src.infra.db.registry_repository import SqlRegistryService
from src.schemas.models import NodeDefinitionRevision, PortTypeRef, RegistrySnapshot

# Singleton durable service.  ``get_session_factory()`` is the production
# composition root — the API no longer constructs in-memory state.
_registry = SqlRegistryService()

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


# ------------------------------------------------------------------
# Request bodies
# ------------------------------------------------------------------


class PortCompatRequest(BaseModel):
    output_port: PortTypeRef
    input_port: PortTypeRef


class RegisterConverterRequest(BaseModel):
    from_schema_id: str
    from_schema_version: int
    to_schema_id: str
    to_schema_version: int
    executor_digest: str


class ActivateDefinitionRequest(BaseModel):
    revision_id: UUID


class RetireDefinitionRequest(BaseModel):
    revision_id: UUID


def _require_platform_admin(key: str | None) -> None:
    """Registry mutation is an internal approved-package control plane."""
    configured = settings.registry_internal_admin_key
    if not configured or not key or not hmac.compare_digest(configured, key):
        raise HTTPException(status_code=403, detail={"error": {"code": "FORBIDDEN", "message": "节点注册表写入仅开放给已配置的平台管理员"}})


# ------------------------------------------------------------------
# Node Definition endpoints
# ------------------------------------------------------------------


@router.post("/definitions", response_model=NodeDefinitionRevision)
async def register_definition(body: NodeDefinitionRevision, x_registry_admin_key: str | None = Header(default=None)) -> NodeDefinitionRevision:
    """Register a new NodeDefinitionRevision (draft status)."""
    try:
        _require_platform_admin(x_registry_admin_key)
        return _registry.add_node_definition(body)
    except (ConflictError, ValidationError_) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/definitions/{node_type_id}/activate", response_model=NodeDefinitionRevision)
async def activate_definition(node_type_id: str, body: ActivateDefinitionRequest, x_registry_admin_key: str | None = Header(default=None)) -> NodeDefinitionRevision:
    """Activate a draft definition."""
    try:
        _require_platform_admin(x_registry_admin_key)
        return _registry.activate_node_definition(node_type_id, body.revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/definitions/{node_type_id}/retire", response_model=NodeDefinitionRevision)
async def retire_definition(node_type_id: str, body: RetireDefinitionRequest, x_registry_admin_key: str | None = Header(default=None)) -> NodeDefinitionRevision:
    """Retire an active definition."""
    try:
        _require_platform_admin(x_registry_admin_key)
        return _registry.retire_node_definition(node_type_id, body.revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/definitions", response_model=list[NodeDefinitionRevision])
async def list_definitions(
    status: str | None = None,
    type_id: str | None = None,
) -> list[NodeDefinitionRevision]:
    """List all node definitions, optionally filtered by status and/or type_id."""
    return _registry.list_node_definitions(status=status, type_id=type_id)


@router.get("/definitions/{node_type_id}", response_model=NodeDefinitionRevision | None)
async def get_definition(node_type_id: str) -> NodeDefinitionRevision | None:
    """Get the active definition for a given type_id."""
    rows = _registry.list_node_definitions(status="active", type_id=node_type_id)
    return rows[0] if rows else None


# ------------------------------------------------------------------
# Port compatibility
# ------------------------------------------------------------------


@router.post("/port-compatibility")
async def check_port_compatibility(body: PortCompatRequest) -> dict[str, Any]:
    """Check if two ports are type-compatible."""
    compatible = _registry.check_port_compatibility(body.output_port, body.input_port)
    msg: str = str(compatible) if compatible else ""
    return {"compatible": bool(compatible), "message": msg}


# ------------------------------------------------------------------
# Converter endpoints
# ------------------------------------------------------------------


@router.post("/converters")
async def register_converter(body: RegisterConverterRequest, x_registry_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Register a type converter."""
    try:
        _require_platform_admin(x_registry_admin_key)
        _registry.add_converter(
            from_schema_id=body.from_schema_id,
            from_schema_version=body.from_schema_version,
            to_schema_id=body.to_schema_id,
            to_schema_version=body.to_schema_version,
            executor_digest=body.executor_digest,
        )
        return {"status": "registered"}
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/converters", response_model=dict[str, Any])
async def list_converters() -> dict[str, Any]:
    """List all registered converters as a map of human-readable key to executor_digest."""
    rows = _registry.list_converters()
    converters: dict[str, str] = {}
    for row in rows:
        from src.infra.db.registry_repository import _converter_blob_key
        converters[_converter_blob_key(
            row.from_schema_id, row.to_schema_id, row.to_schema_version,
        )] = row.executor_digest
    return {"converters": converters}


# ------------------------------------------------------------------
# Registry Snapshot endpoints
# ------------------------------------------------------------------


@router.post("/snapshots", response_model=RegistrySnapshot)
async def generate_snapshot(x_registry_admin_key: str | None = Header(default=None)) -> RegistrySnapshot:
    """Generate a new immutable RegistrySnapshot from active definitions."""
    _require_platform_admin(x_registry_admin_key)
    snapshot, _row = _registry.create_snapshot()
    return snapshot


@router.get("/snapshots", response_model=list[RegistrySnapshot])
async def list_snapshots() -> list[RegistrySnapshot]:
    """List all registry snapshots, newest first."""
    return _registry.list_snapshots()


@router.get("/snapshots/{snapshot_id}", response_model=RegistrySnapshot)
async def get_snapshot(snapshot_id: UUID) -> RegistrySnapshot:
    """Get a specific registry snapshot by ID."""
    try:
        return _registry.get_snapshot(snapshot_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# Node Catalog
# ------------------------------------------------------------------


@router.get("/catalog")
async def catalog(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Active node catalog for the workflow canvas.

    Returns one entry per active NodeDefinitionRevision.  Each entry
    includes the type id, display label, input/output port shapes, and
    the deployment's actual AtlasCloud capability state.  A catalog response
    never exposes credentials; it only reports whether an executor that
    requires a provider can be submitted in this environment.
    """
    defs = _registry.list_node_definitions(status="active")
    atlascloud_configured = bool(settings.atlascloud_api_key)
    nodes = []
    for d in defs:
        provider_required = bool(d.executor_ref)
        nodes.append(
            {
                "type_id": d.node_type_id,
                # The canvas persists this immutable definition revision with
                # every newly-created node.  Compilation verifies it against
                # the RegistrySnapshot instead of silently rebinding a node
                # to a newer definition with the same type id.
                "revision_id": str(d.revision_id),
                "semantic_version": d.semantic_version,
                "executor_ref": d.executor_ref,
                "label": d.node_type_id,
                "input_ports": [p.model_dump(mode="json") for p in d.input_ports],
                "output_ports": [p.model_dump(mode="json") for p in d.output_ports],
                "provider_required": provider_required,
                "execution_available": not provider_required or atlascloud_configured,
            }
        )
    # Active AgentRevisions are owner-scoped dynamic node definitions.  They
    # are supplied by the Agent API so secrets/other owners never leak into a
    # canvas catalog.  A published Agent always exposes its frozen revision id.
    from src.api.routes.agent import list_published_agents
    try:
        published = await list_published_agents(authorization)
        for agent in published["agents"]:
            definition = agent["node_definition"]
            nodes.append({
                "type_id": definition["type_id"], "revision_id": definition["revision_id"],
                "semantic_version": f"agent-r{agent['revision_number']}", "executor_ref": "agent_invoke",
                "label": agent["name"], "description": agent["description"], "category": "Agents",
                "input_ports": definition["input_ports"], "output_ports": definition["output_ports"],
                "config": definition["config"], "provider_required": True,
                "execution_available": atlascloud_configured,
            })
    except Exception:
        # Authentication failures are handled by the Agent endpoint when it is
        # called directly; static registry nodes remain usable during catalog
        # bootstrap.  Never turn a catalog response into cross-owner data.
        pass
    # Published Media Recipes are dynamic, owner-scoped outer nodes. Their
    # private operator DAG never enters this catalog or the canvas.
    try:
        from src.api.auth import require_owner
        from src.infra.db.models import MediaRecipeDefinitionModel, MediaRecipeRevisionModel
        from sqlalchemy import select
        owner_scope = require_owner(authorization)[1].scoped_id
        with _registry._factory() as session:
            rows = session.execute(
                select(MediaRecipeDefinitionModel, MediaRecipeRevisionModel)
                .join(MediaRecipeRevisionModel, MediaRecipeRevisionModel.recipe_id == MediaRecipeDefinitionModel.recipe_id)
                .where(MediaRecipeDefinitionModel.owner_scope == owner_scope, MediaRecipeRevisionModel.status == "active")
                .order_by(MediaRecipeDefinitionModel.name, MediaRecipeRevisionModel.revision_number.desc())
            ).all()
        for definition, revision in rows:
            body = dict(revision.body or {})
            def port(ref: object, index: int, direction: str) -> dict[str, Any]:
                value = str(ref)
                schema_id, marker, version = value.rpartition(".v")
                return {"port_id": f"{direction}_{index}", "label": value, "type_id": "artifact", "schema_id": schema_id if marker and version.isdigit() else "toonflow.media_output", "schema_version": int(version) if marker and version.isdigit() else 1, "cardinality": "optional"}
            revision_id = str(revision.revision_id)
            operator_graph = body.get("operator_graph", {})
            provider_required = any(
                isinstance(step, dict) and str(step.get("type", "")) in {"atlas_llm", "atlas_image", "atlas_video"}
                for step in operator_graph.values()
            ) if isinstance(operator_graph, dict) else True
            nodes.append({
                "type_id": f"media.recipe.{revision_id}", "revision_id": revision_id,
                "semantic_version": f"recipe-r{revision.revision_number}", "executor_ref": "workflow.business.media_recipe_invoke",
                "label": definition.name, "description": definition.description, "category": "Media Recipes",
                "input_ports": [port(ref, index, "input") for index, ref in enumerate(body.get("public_input_schema_refs", []))],
                "output_ports": [port(ref, index, "output") for index, ref in enumerate(body.get("public_output_schema_refs", []))],
                "config": {"media_recipe_revision_id": revision_id}, "provider_required": provider_required,
                "execution_available": not provider_required or atlascloud_configured,
            })
    except Exception:
        # The authenticated static catalog remains usable if a Recipe row is
        # malformed. Compilation repeats the strict frozen-binding checks.
        pass
    return {
        "count": len(nodes),
        "node_types": nodes,
        "provider": {
            "provider_id": "atlascloud",
            "configured": atlascloud_configured,
        },
    }
