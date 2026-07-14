"""Template API Routes — template CRUD, manifest validation, instantiation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.core.exceptions import CrossOwnerError
from src.domain.identity.identity_service import IdentityService
from src.domain.identity.session_service import SessionService
from src.domain.project.project_service import ProjectService
from src.domain.template.template_service import (
    PackageDependency,
    ReplacementSlot,
    TemplateService,
    WorkflowPackageManifest,
)
from src.schemas.enums import DependencyKind
from src.schemas.models import OwnerScope

router = APIRouter(prefix="/api/v1/templates", tags=["template"])

# ---------------------------------------------------------------------------
# Singleton services
# ---------------------------------------------------------------------------

_session_service = SessionService()
_identity_service = IdentityService(session_service=_session_service)
_project_service = ProjectService()
_template_service = TemplateService(project_service=_project_service)


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


class StatusResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_owner(authorization: str | None) -> tuple[Any, OwnerScope]:
    """Validate token and return (account, owner_scope)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if parts[0].lower() != "bearer" or len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = parts[1]
    account, _ = _identity_service.validate_token(token)
    owner_scope = _identity_service._to_owner_scope(str(account.account_id))
    return account, owner_scope


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
async def create_template(body: CreateTemplateRequest):
    """Create a new template (platform operation)."""
    template_id = _template_service.create_template(
        name=body.name,
        workflow_revision_id=body.workflow_revision_id,
        description=body.description,
        parameter_schema=body.parameter_schema,
        default_mapping=body.default_mapping,
        visibility=body.visibility,
    )
    return {"template_id": template_id, "status": "created"}


@router.get("", response_model=list[TemplateSummary])
async def list_templates():
    """List available templates (public/active)."""
    templates = _template_service.list_templates()
    return [TemplateSummary(**t) for t in templates]


@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get full template details."""
    template = _template_service.get_template(template_id)
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


# ---------------------------------------------------------------------------
# Manifest validation (FR-4, FR-5, FR-6)
# ---------------------------------------------------------------------------


@router.post("/manifest/validate", response_model=ValidationResult)
async def validate_manifest(body: ManifestDef):
    """Validate a package manifest."""
    manifest = _manifest_from_def(body)
    errors = _template_service.validate_manifest(manifest)
    return ValidationResult(valid=len(errors) == 0, errors=errors)


# ---------------------------------------------------------------------------
# Dependency resolution (FR-6, FR-7, FR-12)
# ---------------------------------------------------------------------------


@router.post("/{template_id}/resolve-dependencies", response_model=DependencyResult)
async def resolve_dependencies(template_id: str, replacements: dict[str, str] | None = None):
    """Check and resolve dependencies for a template."""
    result = _template_service.resolve_dependencies(
        template_id=template_id,
        replacements=replacements,
    )
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


# ---------------------------------------------------------------------------
# Instance queries
# ---------------------------------------------------------------------------


@router.get("/instances/{instance_id}", response_model=InstanceResponse)
async def get_instance(instance_id: str):
    """Get instantiation record."""
    instance = _template_service.get_instance(instance_id)
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
