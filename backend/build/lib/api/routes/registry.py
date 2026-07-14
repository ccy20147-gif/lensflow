"""TF-WF-002: Registry API Routes

Endpoints for node definition registration, activation, retirement,
querying, converter management, and registry snapshots.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.core.exceptions import NotFoundError, ConflictError, ValidationError_
from src.schemas.models import NodeDefinitionRevision, PortTypeRef, RegistrySnapshot

from src.domain.workflow.registry_service import RegistryService

# Singleton service instance (Foundation: in-memory)
_registry = RegistryService()

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


# ------------------------------------------------------------------
# Node Definition endpoints
# ------------------------------------------------------------------


@router.post("/definitions", response_model=NodeDefinitionRevision)
async def register_definition(body: NodeDefinitionRevision) -> NodeDefinitionRevision:
    """Register a new NodeDefinitionRevision (draft status)."""
    try:
        return _registry.register_definition(body)
    except (ConflictError, ValidationError_) as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/definitions/{node_type_id}/activate")
async def activate_definition(node_type_id: str, revision_id: UUID) -> NodeDefinitionRevision:
    """Activate a draft definition."""
    try:
        return _registry.activate_definition(node_type_id, revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.post("/definitions/{node_type_id}/retire")
async def retire_definition(node_type_id: str, revision_id: UUID) -> NodeDefinitionRevision:
    """Retire an active definition."""
    try:
        return _registry.retire_definition(node_type_id, revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/definitions", response_model=list[NodeDefinitionRevision])
async def list_definitions(
    status: str | None = None,
    type_id: str | None = None,
) -> list[NodeDefinitionRevision]:
    """List all node definitions, optionally filtered by status and/or type_id."""
    return _registry.list_definitions(status=status, type_id_filter=type_id)


@router.get("/definitions/{node_type_id}", response_model=NodeDefinitionRevision | None)
async def get_definition(
    node_type_id: str, revision_id: UUID | None = None
) -> NodeDefinitionRevision | None:
    """Get a specific definition by type_id and optional revision_id (active if None)."""
    try:
        return _registry.get_definition(node_type_id, revision_id)
    except NotFoundError as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


# ------------------------------------------------------------------
# Port compatibility
# ------------------------------------------------------------------


@router.post("/port-compatibility")
async def check_port_compatibility(
    output_port: PortTypeRef, input_port: PortTypeRef
) -> dict[str, Any]:
    """Check if two ports are type-compatible."""
    compatible = _registry.check_port_compatibility(output_port, input_port)
    return {"compatible": compatible}


# ------------------------------------------------------------------
# Converter endpoints
# ------------------------------------------------------------------


@router.post("/converters")
async def register_converter(
    from_schema_id: str,
    from_schema_version: int,
    to_schema_id: str,
    to_schema_version: int,
    executor_digest: str,
) -> dict[str, Any]:
    """Register a type converter."""
    try:
        _registry.register_converter(
            from_schema_id, from_schema_version,
            to_schema_id, to_schema_version,
            executor_digest,
        )
        return {"status": "registered"}
    except ValidationError_ as e:
        raise HTTPException(status_code=e.status_code, detail=e.to_dict())


@router.get("/converters", response_model=dict[str, Any])
async def list_converters() -> dict[str, Any]:
    """List all registered converters."""
    return {"converters": _registry.list_converters()}


# ------------------------------------------------------------------
# Registry Snapshot endpoints
# ------------------------------------------------------------------


@router.post("/snapshots", response_model=RegistrySnapshot)
async def generate_snapshot() -> RegistrySnapshot:
    """Generate a new immutable RegistrySnapshot from active definitions."""
    return _registry.generate_snapshot()


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
