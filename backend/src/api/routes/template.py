"""Template API Routes — template CRUD, manifest validation, instantiation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import CrossOwnerError, NotFoundError, SafeError
from src.domain.template.template_service import (
    PackageDependency,
    ReplacementSlot,
    WorkflowPackageManifest,
)
from src.infra.db.identity_repository import get_session_store
from src.infra.db.template_repository import SqlTemplateService
from src.schemas.enums import DependencyKind
from src.schemas.models import OwnerScope
from src.core.config import settings
import hmac
from sqlalchemy import select
from src.infra.db.models import ResourceModel, ResourceRevisionModel, WorkflowModel, WorkflowRevisionModel
from src.infra.db.session import get_session_factory

router = APIRouter(prefix="/api/v1/templates", tags=["template"])

# ---------------------------------------------------------------------------
# Singleton services
# ---------------------------------------------------------------------------

_sessions = get_session_store()
_template_service = SqlTemplateService()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateTemplateRequest(BaseModel):
    name: str
    description: str = ""
    workflow_revision_id: str = ""
    parameter_schema: dict[str, Any] = {}
    default_mapping: dict[str, Any] = {}
    visibility: str = "public"
    manifest: ManifestDef | None = None


class DependencyDef(BaseModel):
    dep_id: str
    kind: str
    revision_id: str
    name: str = ""
    schema_id: str = ""
    inclusion_mode: str = "required"
    grant_required: bool = False
    capability_requirements: list[str] = []
    replacement_slot: str | None = None


class SlotDef(BaseModel):
    slot_id: str
    label: str
    description: str = ""
    expected_kind: str = "resource"
    required: bool = True


class ManifestDef(BaseModel):
    name: str
    version: str = "1.0.0"
    dependencies: list[DependencyDef] = []
    replacement_slots: list[SlotDef] = []
    parameter_schema: dict[str, Any] = {}
    description: str = ""


class InstantiateRequest(BaseModel):
    project_name: str = ""
    project_description: str = ""
    parameters: dict[str, Any] = {}
    replacements: dict[str, str] = {}


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None


class TemplateSummary(BaseModel):
    template_id: str
    name: str
    description: str
    visibility: str
    provenance: str
    revision_status: str
    parameter_schema: dict[str, Any]
    created_at: str


class InstanceResponse(BaseModel):
    instance_id: str
    template_id: str
    template_revision_id: str
    project_id: str
    workflow_id: str
    dependency_resolution: dict[str, str]
    replacement_mapping: dict[str, str]
    attribution_manifest: dict[str, Any]
    created_at: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str]


class DependencyResult(BaseModel):
    resolved: bool
    missing: list[str]
    unresolved_slots: list[str]
    available: bool
    resolution: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    closure: list[dict[str, Any]] = []


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> tuple[Any, OwnerScope]:
    """Validate a durable session and return its owner scope."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if parts[0].lower() != "bearer" or len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = parts[1]
    try:
        account_id = _sessions.account_for_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    return account_id, OwnerScope(kind="user", id=account_id)


def _safe_error_response(exc: SafeError) -> HTTPException:
    """Expose typed package diagnostics without leaking internals."""
    payload = exc.to_dict()
    if exc.details:
        payload["error"]["details"] = exc.details
    return HTTPException(status_code=exc.status_code, detail=payload)


def _require_template_maintainer(key: str | None) -> None:
    """Fail closed: official template mutation belongs to the platform."""
    if not settings.template_internal_admin_key or not key or not hmac.compare_digest(key, settings.template_internal_admin_key):
        raise HTTPException(status_code=403, detail="Template maintainer credential required")


def _manifest_from_def(defn: ManifestDef) -> WorkflowPackageManifest:
    deps = [
        PackageDependency(
            dep_id=d.dep_id,
            kind=DependencyKind(d.kind),
            revision_id=d.revision_id,
            name=d.name,
            schema_id=d.schema_id,
            inclusion_mode=d.inclusion_mode,
            grant_required=d.grant_required,
            capability_requirements=d.capability_requirements,
            replacement_slot=d.replacement_slot,
        )
        for d in defn.dependencies
    ]
    slots = [
        ReplacementSlot(
            slot_id=s.slot_id,
            label=s.label,
            description=s.description,
            expected_kind=DependencyKind(s.expected_kind),
            required=s.required,
        )
        for s in defn.replacement_slots
    ]
    return WorkflowPackageManifest(
        name=defn.name,
        version=defn.version,
        dependencies=deps,
        replacement_slots=slots,
        parameter_schema=defn.parameter_schema,
        description=defn.description,
    )


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_template(body: CreateTemplateRequest, authorization: str | None = Header(None), x_template_admin_key: str | None = Header(None)):
    """Create a new template (platform operation)."""
    try:
        _require_template_maintainer(x_template_admin_key)
        _, owner = _resolve_owner(authorization)
        template_id = _template_service.create_template(
            name=body.name,
            workflow_revision_id=body.workflow_revision_id,
            manifest=_manifest_from_def(body.manifest) if body.manifest else None,
            description=body.description,
            parameter_schema=body.parameter_schema,
            default_mapping=body.default_mapping,
            visibility=body.visibility,
            owner_scope=owner,
        )
        return {"template_id": template_id, "status": "created"}
    except SafeError as exc:
        raise _safe_error_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}) from exc


@router.post("/benchmarks/seed", status_code=201)
async def seed_benchmark_templates(authorization: str | None = Header(None), x_template_admin_key: str | None = Header(None)) -> dict[str, list[str]]:
    _require_template_maintainer(x_template_admin_key)
    _, owner = _resolve_owner(authorization)
    return {"template_ids": _template_service.seed_benchmark_templates(owner)}


@router.get("", response_model=list[TemplateSummary])
async def list_templates(authorization: str | None = Header(None)):
    """List public templates plus the current owner's private packages."""
    _, owner = _resolve_owner(authorization)
    templates = _template_service.list_templates(owner)
    return [TemplateSummary(**t) for t in templates]


@router.get("/{template_id}/replacement-options")
async def replacement_options(template_id: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Owner-scoped immutable revision choices for typed replacement slots."""
    _, owner = _resolve_owner(authorization)
    template = _template_service.get_template(template_id, owner)
    slots: list[dict[str, Any]] = []
    with get_session_factory()() as session:
        for slot in template.manifest.replacement_slots:
            candidates: list[dict[str, str]] = []
            if slot.expected_kind == DependencyKind.RESOURCE:
                rows = session.execute(select(ResourceRevisionModel, ResourceModel).join(
                    ResourceModel, ResourceModel.resource_id == ResourceRevisionModel.resource_id,
                ).where(ResourceModel.owner_scope == owner.scoped_id)).all()
                candidates = [{"revision_id": str(revision.revision_id), "label": f"{resource.resource_type} · r{revision.revision_number}"} for revision, resource in rows]
            elif slot.expected_kind == DependencyKind.WORKFLOW:
                rows = session.execute(select(WorkflowRevisionModel, WorkflowModel).join(
                    WorkflowModel, WorkflowModel.workflow_id == WorkflowRevisionModel.workflow_id,
                ).where(WorkflowModel.owner_scope == owner.scoped_id, WorkflowRevisionModel.revision_status == "active")).all()
                candidates = [{"revision_id": str(revision.revision_id), "label": f"Workflow · r{revision.revision_number}"} for revision, _workflow in rows]
            slots.append({"slot_id": slot.slot_id, "expected_kind": slot.expected_kind.value, "candidates": candidates})
    return {"slots": slots}


@router.get("/{template_id}")
async def get_template(template_id: str, authorization: str | None = Header(None)):
    """Get full template details."""
    try:
        _, owner = _resolve_owner(authorization)
        template = _template_service.get_template(template_id, owner)
    except NotFoundError as exc:
        raise _safe_error_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Template 未找到"}}) from exc
    return {
        "template_id": template.template_id,
        "name": template.name,
        "description": template.description,
        "workflow_revision_id": template.workflow_revision_id,
        "manifest": template.manifest.to_dict() if template.manifest else None,
        "parameter_schema": template.parameter_schema,
        "default_mapping": template.default_mapping,
        "visibility": template.visibility,
        "provenance": template.provenance,
        "revision_status": template.revision_status.value,
        "created_at": template.created_at.isoformat(),
        "updated_at": template.updated_at.isoformat(),
    }


@router.patch("/{template_id}")
async def update_template(template_id: str, body: UpdateTemplateRequest, authorization: str | None = Header(None)):
    """Only the template maintainer may change non-package metadata."""
    _, owner = _resolve_owner(authorization)
    try:
        template = _template_service.update_template(template_id, owner, name=body.name, description=body.description, visibility=body.visibility)
    except NotFoundError as exc:
        raise _safe_error_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "Template 未找到"}}) from exc
    return {"template_id": template.template_id, "name": template.name, "description": template.description, "visibility": template.visibility}


# ---------------------------------------------------------------------------
# Manifest validation (FR-4, FR-5, FR-6)
# ---------------------------------------------------------------------------


@router.post("/manifest/validate", response_model=ValidationResult)
async def validate_manifest(body: ManifestDef, authorization: str | None = Header(None)):
    """Validate a package manifest."""
    _resolve_owner(authorization)
    manifest = _manifest_from_def(body)
    errors = _template_service.validate_manifest(manifest)
    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# Dependency resolution (FR-6, FR-7, FR-12)
# ---------------------------------------------------------------------------


@router.post("/{template_id}/resolve-dependencies", response_model=DependencyResult)
async def resolve_dependencies(template_id: str, replacements: dict[str, str] | None = None, authorization: str | None = Header(None)):
    """Check and resolve dependencies for a template."""
    _, owner = _resolve_owner(authorization)
    try:
        result = _template_service.resolve_dependencies(
            template_id=template_id,
            replacements=replacements,
            owner_scope=owner,
        )
    except NotFoundError as exc:
        raise _safe_error_response(exc) from exc
    return DependencyResult(**result)


# ---------------------------------------------------------------------------
# Instantiation (FR-3, FR-9, FR-11)
# ---------------------------------------------------------------------------


@router.post("/{template_id}/instantiate", response_model=InstanceResponse, status_code=201)
async def instantiate_template(
    template_id: str,
    body: InstantiateRequest,
    authorization: str | None = Header(None),
):
    """Create a new project + workflow draft from a template."""
    _, owner = _resolve_owner(authorization)
    try:
        instance = _template_service.instantiate_template(
            template_id=template_id,
            owner_scope=owner,
            project_name=body.project_name,
            project_description=body.project_description,
            parameters=body.parameters,
            replacements=body.replacements,
        )
        return InstanceResponse(
            instance_id=instance.instance_id,
            template_id=instance.template_id,
            template_revision_id=instance.template_revision_id,
            project_id=instance.project_id,
            workflow_id=instance.workflow_id,
            dependency_resolution=instance.dependency_resolution,
            replacement_mapping=instance.replacement_mapping,
            attribution_manifest=instance.attribution_manifest or {},
            created_at=instance.created_at.isoformat(),
        )
    except CrossOwnerError:
        raise HTTPException(status_code=403, detail="Cross-owner access denied")
    except SafeError as exc:
        raise _safe_error_response(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}) from exc


# ---------------------------------------------------------------------------
# Instance queries
# ---------------------------------------------------------------------------


@router.get("/instances/{instance_id}", response_model=InstanceResponse)
async def get_instance(instance_id: str, authorization: str | None = Header(None)):
    """Get instantiation record."""
    _, owner = _resolve_owner(authorization)
    instance = _template_service.get_instance(instance_id, owner)
    return InstanceResponse(
        instance_id=instance.instance_id,
        template_id=instance.template_id,
        template_revision_id=instance.template_revision_id,
        project_id=instance.project_id,
        workflow_id=instance.workflow_id,
        dependency_resolution=instance.dependency_resolution,
        replacement_mapping=instance.replacement_mapping,
        attribution_manifest=instance.attribution_manifest or {},
        created_at=instance.created_at.isoformat(),
    )
