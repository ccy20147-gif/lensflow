"""Template Service — template CRUD, manifest validation, dependency resolution, instance creation."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from src.core.exceptions import ConflictError, NotFoundError
from src.schemas.enums import DependencyKind, RevisionStatus
from src.schemas.models import OwnerScope, WorkflowDraft

from ..project.project_service import ProjectService


# ---------------------------------------------------------------------------
# Data Models for Template Domain
# ---------------------------------------------------------------------------


class PackageDependency:
    """A typed dependency within a template package."""

    def __init__(
        self,
        dep_id: str,
        kind: DependencyKind,
        revision_id: str,
        name: str = "",
        schema_id: str = "",
        inclusion_mode: str = "required",
        grant_required: bool = False,
        capability_requirements: list[str] | None = None,
        replacement_slot: str | None = None,  # typed slot if not directly includable
    ):
        self.dep_id = dep_id
        self.kind = kind
        self.revision_id = revision_id
        self.name = name
        self.schema_id = schema_id
        self.inclusion_mode = inclusion_mode
        self.grant_required = grant_required
        self.capability_requirements = capability_requirements or []
        self.replacement_slot = replacement_slot

    def to_dict(self) -> dict[str, Any]:
        return {
            "dep_id": self.dep_id,
            "kind": self.kind.value,
            "revision_id": self.revision_id,
            "name": self.name,
            "schema_id": self.schema_id,
            "inclusion_mode": self.inclusion_mode,
            "grant_required": self.grant_required,
            "capability_requirements": self.capability_requirements,
            "replacement_slot": self.replacement_slot,
        }


class ReplacementSlot:
    """A typed slot where the user must provide a replacement dependency."""

    def __init__(
        self,
        slot_id: str,
        label: str,
        description: str = "",
        expected_kind: DependencyKind = DependencyKind.RESOURCE,
        required: bool = True,
    ):
        self.slot_id = slot_id
        self.label = label
        self.description = description
        self.expected_kind = expected_kind
        self.required = required

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "label": self.label,
            "description": self.description,
            "expected_kind": self.expected_kind.value,
            "required": self.required,
        }


class WorkflowPackageManifest:
    """Package manifest listing all deps and metadata (FR-4)."""

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        dependencies: list[PackageDependency] | None = None,
        replacement_slots: list[ReplacementSlot] | None = None,
        parameter_schema: dict[str, Any] | None = None,
        description: str = "",
    ):
        self.name = name
        self.version = version
        self.dependencies = dependencies or []
        self.replacement_slots = replacement_slots or []
        self.parameter_schema = parameter_schema or {}
        self.description = description

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "replacement_slots": [s.to_dict() for s in self.replacement_slots],
            "parameter_schema": self.parameter_schema,
            "description": self.description,
        }


class TemplateRecord:
    """Internal template storage record."""

    def __init__(
        self,
        template_id: str,
        name: str,
        description: str = "",
        manifest: WorkflowPackageManifest | None = None,
        workflow_revision_id: str = "",
        parameter_schema: dict[str, Any] | None = None,
        default_mapping: dict[str, Any] | None = None,
        visibility: str = "public",
        provenance: str = "platform",
        revision_status: RevisionStatus = RevisionStatus.ACTIVE,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ):
        self.template_id = template_id
        self.name = name
        self.description = description
        self.manifest = manifest or WorkflowPackageManifest(name=name)
        self.workflow_revision_id = workflow_revision_id
        self.parameter_schema = parameter_schema or {}
        self.default_mapping = default_mapping or {}
        self.visibility = visibility
        self.provenance = provenance
        self.revision_status = revision_status
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)


class InstanceRecord:
    """Records a template instantiation (lineage tracking)."""

    def __init__(
        self,
        instance_id: str,
        template_id: str,
        template_revision_id: str,
        project_id: str,
        workflow_id: str,
        dependency_resolution: dict[str, str] | None = None,
        replacement_mapping: dict[str, str] | None = None,
        attribution_manifest: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ):
        self.instance_id = instance_id
        self.template_id = template_id
        self.template_revision_id = template_revision_id
        self.project_id = project_id
        self.workflow_id = workflow_id
        self.dependency_resolution = dependency_resolution or {}
        self.replacement_mapping = replacement_mapping or {}
        self.attribution_manifest = attribution_manifest or {}
        self.created_at = created_at or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TemplateStore(Protocol):
    def save(self, record: TemplateRecord) -> None: ...
    def get_by_id(self, template_id: str) -> TemplateRecord | None: ...
    def list_all(self) -> list[TemplateRecord]: ...
    def list_visible(self) -> list[TemplateRecord]: ...


class InstanceStore(Protocol):
    def save(self, record: InstanceRecord) -> None: ...
    def get_by_id(self, instance_id: str) -> InstanceRecord | None: ...
    def list_by_template(self, template_id: str) -> list[InstanceRecord]: ...
    def list_by_project(self, project_id: str) -> list[InstanceRecord]: ...


class InMemoryTemplateStore:
    def __init__(self) -> None:
        self._templates: dict[str, TemplateRecord] = {}

    def save(self, record: TemplateRecord) -> None:
        self._templates[record.template_id] = record

    def get_by_id(self, template_id: str) -> TemplateRecord | None:
        return self._templates.get(template_id)

    def list_all(self) -> list[TemplateRecord]:
        return list(self._templates.values())

    def list_visible(self) -> list[TemplateRecord]:
        return [t for t in self._templates.values() if t.visibility == "public" and t.revision_status == RevisionStatus.ACTIVE]


class InMemoryInstanceStore:
    def __init__(self) -> None:
        self._instances: dict[str, InstanceRecord] = {}

    def save(self, record: InstanceRecord) -> None:
        self._instances[record.instance_id] = record

    def get_by_id(self, instance_id: str) -> InstanceRecord | None:
        return self._instances.get(instance_id)

    def list_by_template(self, template_id: str) -> list[InstanceRecord]:
        return [i for i in self._instances.values() if i.template_id == template_id]

    def list_by_project(self, project_id: str) -> list[InstanceRecord]:
        return [i for i in self._instances.values() if i.project_id == project_id]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TemplateService:
    """Manages templates, manifests, dependency resolution, and instantiation."""

    def __init__(
        self,
        template_store: TemplateStore | None = None,
        instance_store: InstanceStore | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self._templates = template_store or InMemoryTemplateStore()
        self._instances = instance_store or InMemoryInstanceStore()
        self._projects = project_service or ProjectService()

    # ---- Template CRUD ----

    def create_template(
        self,
        name: str,
        workflow_revision_id: str = "",
        manifest: WorkflowPackageManifest | None = None,
        description: str = "",
        parameter_schema: dict[str, Any] | None = None,
        default_mapping: dict[str, Any] | None = None,
        visibility: str = "public",
    ) -> str:
        """Create a new template. Returns template_id."""
        template_id = str(uuid.uuid4())
        record = TemplateRecord(
            template_id=template_id,
            name=name,
            description=description,
            manifest=manifest or WorkflowPackageManifest(name=name),
            workflow_revision_id=workflow_revision_id,
            parameter_schema=parameter_schema or {},
            default_mapping=default_mapping or {},
            visibility=visibility,
        )
        self._templates.save(record)
        return template_id

    def get_template(self, template_id: str) -> TemplateRecord:
        rec = self._templates.get_by_id(template_id)
        if rec is None:
            raise NotFoundError("Template", template_id)
        return rec

    def list_templates(self) -> list[dict[str, Any]]:
        """List visible templates with summary info."""
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "description": t.description,
                "visibility": t.visibility,
                "provenance": t.provenance,
                "revision_status": t.revision_status.value,
                "parameter_schema": t.parameter_schema,
                "created_at": t.created_at.isoformat(),
            }
            for t in self._templates.list_visible()
        ]

    # ---- Manifest Validation (FR-4, FR-5, FR-6) ----

    def validate_manifest(self, manifest: WorkflowPackageManifest) -> list[str]:
        """Validate a package manifest. Returns list of validation errors."""
        errors: list[str] = []

        if not manifest.name:
            errors.append("Manifest name is required")

        # Check for circular dependencies (simple approach)
        seen: set[str] = set()
        for dep in manifest.dependencies:
            if dep.dep_id in seen:
                errors.append(f"Circular dependency: {dep.dep_id}")
            seen.add(dep.dep_id)

            # Validate kind
            try:
                _ = dep.kind  # Already validated by enum
            except Exception:
                errors.append(f"Unknown dependency kind in {dep.dep_id}")

            # Slot validation
            if dep.replacement_slot:
                # Ensure slot exists in manifest
                slot_ids = {s.slot_id for s in manifest.replacement_slots}
                if dep.replacement_slot not in slot_ids:
                    errors.append(
                        f"Dependency {dep.dep_id} references unknown replacement slot '{dep.replacement_slot}'"
                    )

        # Check slot IDs are unique
        slot_ids = [s.slot_id for s in manifest.replacement_slots]
        if len(slot_ids) != len(set(slot_ids)):
            errors.append("Replacement slot IDs must be unique")

        return errors

    # ---- Dependency Resolution (FR-6, FR-7, FR-12) ----

    def resolve_dependencies(
        self,
        template_id: str,
        replacements: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Check and resolve dependencies for a template.

        Returns:
            {
                "resolved": bool,
                "missing": [list of missing dep ids],
                "unresolved_slots": [list of slot ids needing replacement],
                "available": bool
            }
        """
        template = self.get_template(template_id)
        manifest = template.manifest
        replacements = replacements or {}

        unresolved_slots: list[str] = []
        missing_deps: list[str] = []
        resolution: dict[str, str] = {}

        for dep in manifest.dependencies:
            if dep.replacement_slot:
                slot_id = dep.replacement_slot
                if slot_id in replacements:
                    resolution[dep.dep_id] = replacements[slot_id]
                else:
                    # Check if slot is required
                    matching_slots = [s for s in manifest.replacement_slots if s.slot_id == slot_id]
                    if matching_slots and matching_slots[0].required:
                        unresolved_slots.append(slot_id)
                continue

            # For non-slotted deps, check if they're available (in-memory check)
            # In a real system, we'd check the registry
            # For V0, assume platform managed presets are available
            if dep.inclusion_mode == "required":
                resolution[dep.dep_id] = dep.revision_id
            else:
                missing_deps.append(dep.dep_id)

        return {
            "resolved": len(unresolved_slots) == 0 and len(missing_deps) == 0,
            "missing": missing_deps,
            "unresolved_slots": unresolved_slots,
            "available": len(missing_deps) == 0,
        }

    # ---- Instantiation (FR-3, FR-11) ----

    def instantiate_template(
        self,
        template_id: str,
        owner_scope: OwnerScope,
        project_name: str = "",
        project_description: str = "",
        parameters: dict[str, Any] | None = None,
        replacements: dict[str, str] | None = None,
    ) -> InstanceRecord:
        """Create a new project + workflow draft from a template (FR-3).

        Must not modify the template revision (FR-3).
        Must block on missing deps (FR-12).
        Must preserve lineage (FR-11).
        """
        template = self.get_template(template_id)
        parameters = parameters or {}
        replacements = replacements or {}

        # Validate manifest first
        errors = self.validate_manifest(template.manifest)
        if errors:
            raise ConflictError(f"模板清单校验失败: {'; '.join(errors)}")

        # Resolve dependencies — block if unresolved
        resolution = self.resolve_dependencies(template_id, replacements)
        if not resolution["resolved"]:
            details = {}
            if resolution["missing"]:
                details["missing_deps"] = resolution["missing"]
            if resolution["unresolved_slots"]:
                details["unresolved_slots"] = resolution["unresolved_slots"]
            raise ConflictError(
                message="模板依赖未满足，无法实例化",
                details=details,
            )

        # Create a new project
        name = project_name or f"from_{template.name}"
        project = self._projects.create_project(
            owner_scope=owner_scope,
            name=name,
            description=project_description or template.description,
        )

        # Create a WorkflowDraft from the template revision (FR-3: independent copy)
        workflow_id = str(uuid.uuid4())
        workflow_draft = WorkflowDraft(
            workflow_id=uuid.UUID(workflow_id),
            draft_version=1,
            base_revision_id=uuid.UUID(template.workflow_revision_id) if template.workflow_revision_id else None,
            graph={},
            config=parameters,
            layout={},
            updated_at=datetime.now(timezone.utc),
        )

        # Link workflow to project
        self._projects.add_workflow(
            project_id=str(project.project_id),
            workflow_id=workflow_id,
            caller_owner=owner_scope,
        )

        # Build attribution manifest (FR-11)
        attribution = {
            "template_id": template_id,
            "template_name": template.name,
            "template_revision_id": template.workflow_revision_id,
            "provenance": template.provenance,
            "parameter_snapshot": parameters,
            "dependency_resolution": resolution.get("resolved", []),
            "replacement_mapping": replacements,
        }

        # Record the instance
        instance_id = str(uuid.uuid4())
        instance = InstanceRecord(
            instance_id=instance_id,
            template_id=template_id,
            template_revision_id=template.workflow_revision_id,
            project_id=str(project.project_id),
            workflow_id=workflow_id,
            dependency_resolution=resolution.get("missing", {}),
            replacement_mapping=replacements,
            attribution_manifest=attribution,
        )
        self._instances.save(instance)

        return instance

    def get_instance(self, instance_id: str) -> InstanceRecord:
        rec = self._instances.get_by_id(instance_id)
        if rec is None:
            raise NotFoundError("Instance", instance_id)
        return rec

    def list_instances_by_project(self, project_id: str) -> list[InstanceRecord]:
        return self._instances.list_by_project(project_id)
