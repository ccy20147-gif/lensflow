"""PostgreSQL implementation of TF-WF-009 template packages.

The in-memory TemplateService remains a unit-test double.  API composition
uses this service so a template package, its cloned workflow draft, and the
lineage record survive a restart as one database transaction.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, PolicyBlockedError, ValidationError_
from src.domain.template.template_service import (
    InstanceRecord,
    PackageDependency,
    ReplacementSlot,
    TemplateRecord,
    TemplateService,
    WorkflowPackageManifest,
)
from src.domain.workflow.draft_revision import compute_draft_hashes
from src.domain.workflow.compiler import CompilationError, WorkflowCompiler
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.models import (
    AgentDefinitionModel,
    AgentRevisionModel,
    MediaRecipeDefinitionModel,
    NodeDefinitionModel,
    MediaRecipeRevisionModel,
    ProjectModel,
    WorkflowDraftModel,
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowTemplateInstanceModel,
    WorkflowTemplateModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
    SkillRevisionModel,
    SkillContentModel,
    ConverterRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import DependencyKind, ProjectStatus, RevisionStatus
from src.schemas.models import OwnerScope


BENCHMARK_TEMPLATE_GRAPHS: dict[str, dict[str, Any]] = {
    "广告创意候选与人工精修": {
        "nodes": [
            {"id": "brief", "type": "brief", "config": {"brief": {"goal": "广告创意候选", "audience": "消费者"}}},
            {"id": "constraints", "type": "constraint", "config": {"constraints": [{"format": "1:1", "tone": "明快"}]}},
            {"id": "generate", "type": "structured_generate", "config": {"json_schema": {"type": "object", "required": ["concept"], "properties": {"concept": {"type": "string"}}}, "output": {"concept": "城市晨光"}, "schema_id": "creative_concept"}},
            {"id": "variants", "type": "variants", "config": {"candidate_payloads": [{"title": "候选 A"}, {"title": "候选 B"}], "candidate_schema_id": "creative_candidate"}},
            {"id": "select", "type": "select_rank", "config": {"rubric_revision": "benchmark.v1"}},
            {"id": "retouch", "type": "workbench_task", "config": {"target_workbench": "创意精修", "output_schema_ref": "workbench_result.v1", "resource_type": "creative_board"}},
            {"id": "review", "type": "review", "config": {"issues": []}},
            {"id": "export", "type": "package_export", "config": {"artifact_version_ids": []}},
        ],
        "edges": [
            {"source": source, "sourceHandle": "out", "target": target, "targetHandle": "in"}
            for source, target in [("brief", "constraints"), ("constraints", "generate"), ("generate", "variants"), ("variants", "select"), ("select", "retouch"), ("retouch", "review"), ("review", "export")]
        ],
    },
    "镜头计划与分镜提交": {
        "nodes": [
            {"id": "brief", "type": "brief", "config": {"brief": {"goal": "镜头计划", "audience": "导演组"}}},
            {"id": "constraints", "type": "constraint", "config": {"constraints": [{"format": "16:9", "duration_seconds": 30}]}},
            {"id": "shot_plan", "type": "structured_generate", "config": {"json_schema": {"type": "object", "required": ["shots"], "properties": {"shots": {"type": "array"}}}, "output": {"shots": [{"id": "s1", "framing": "wide"}]}, "schema_id": "shot_plan"}},
            {"id": "router", "type": "model_router", "config": {"provider_selection_policy_ref": "atlascloud.benchmark.v1", "enabled_models": ["atlascloud/image"]}},
            {"id": "variants", "type": "variants", "config": {"candidate_payloads": [{"title": "分镜 A"}, {"title": "分镜 B"}], "candidate_schema_id": "storyboard_candidate"}},
            {"id": "select", "type": "select_rank", "config": {"rubric_revision": "benchmark.v1"}},
            {"id": "storyboard", "type": "workbench_task", "config": {"target_workbench": "故事板", "output_schema_ref": "workbench_result.v1", "resource_type": "storyboard"}},
            {"id": "review", "type": "review", "config": {"issues": []}},
            {"id": "export", "type": "package_export", "config": {"artifact_version_ids": []}},
        ],
        "edges": [
            {"source": source, "sourceHandle": "out", "target": target, "targetHandle": "in"}
            for source, target in [("brief", "constraints"), ("constraints", "shot_plan"), ("shot_plan", "router"), ("router", "variants"), ("variants", "select"), ("select", "storyboard"), ("storyboard", "review"), ("review", "export")]
        ],
    },
}


def _benchmark_content_hash(graph: dict[str, Any]) -> str:
    """Return the stable, semantic version of an official benchmark graph.

    Benchmark packages are immutable pins to a WorkflowRevision.  A package
    name alone therefore cannot tell whether a prior seed is still current:
    historically it allowed an untyped-edge revision to be reused forever.
    Keep the version derived from canonical graph content rather than from an
    incidental database ID or deployment time.
    """
    encoded = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


BENCHMARK_TEMPLATE_CONTENT_HASHES: dict[str, str] = {
    name: _benchmark_content_hash(graph)
    for name, graph in BENCHMARK_TEMPLATE_GRAPHS.items()
}


def _manifest_from_json(value: dict[str, Any]) -> WorkflowPackageManifest:
    dependencies = [
        PackageDependency(
            dep_id=str(item["dep_id"]), kind=DependencyKind(str(item["kind"])),
            revision_id=str(item["revision_id"]), name=str(item.get("name", "")),
            schema_id=str(item.get("schema_id", "")), inclusion_mode=str(item.get("inclusion_mode", "required")),
            grant_required=bool(item.get("grant_required", False)),
            capability_requirements=list(item.get("capability_requirements", [])),
            replacement_slot=item.get("replacement_slot"),
        )
        for item in value.get("dependencies", [])
    ]
    slots = [
        ReplacementSlot(
            slot_id=str(item["slot_id"]), label=str(item["label"]),
            description=str(item.get("description", "")),
            expected_kind=DependencyKind(str(item.get("expected_kind", "resource"))),
            required=bool(item.get("required", True)),
        )
        for item in value.get("replacement_slots", [])
    ]
    return WorkflowPackageManifest(
        name=str(value.get("name", "")), version=str(value.get("version", "1.0.0")),
        dependencies=dependencies, replacement_slots=slots,
        parameter_schema=dict(value.get("parameter_schema", {})), description=str(value.get("description", "")),
    )


def _record(row: WorkflowTemplateModel) -> TemplateRecord:
    return TemplateRecord(
        template_id=str(row.template_id), name=row.name, description=row.description,
        manifest=_manifest_from_json(row.manifest or {}), workflow_revision_id=str(row.workflow_revision_id),
        parameter_schema=row.parameter_schema or {}, default_mapping=row.default_mapping or {},
        visibility=row.visibility, provenance=row.provenance, revision_status=row.revision_status,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _instance(row: WorkflowTemplateInstanceModel) -> InstanceRecord:
    return InstanceRecord(
        instance_id=str(row.instance_id), template_id=str(row.template_id),
        template_revision_id=str(row.template_revision_id), project_id=str(row.project_id), workflow_id=str(row.workflow_id),
        dependency_resolution=row.dependency_resolution or {}, replacement_mapping=row.replacement_mapping or {},
        attribution_manifest=row.attribution_manifest or {}, created_at=row.created_at,
    )


_SECRET_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{12,}|AIza[\w-]{16,}|(?:api[_-]?key|token|secret)\s*[:=]|https?://|\b(?:curl|wget|bash|sh)\b)", re.IGNORECASE)


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in {"secret", "credentialbinding", "credential_binding", "api_key", "token", "authorization", "password"} or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return isinstance(value, str) and bool(_SECRET_VALUE.search(value))


class SqlTemplateService(TemplateService):
    """Durable template package service; public methods match TemplateService."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        # Don't call TemplateService.__init__: its in-memory stores must never
        # accidentally become an API fallback.
        self._factory = factory or get_session_factory()

    def seed_benchmark_templates(self, owner_scope: OwnerScope) -> list[str]:
        """Upsert the current immutable official benchmark packages per owner.

        An older package is deliberately *retired*, not overwritten, when its
        pinned revision no longer matches the canonical benchmark graph.  This
        preserves provenance for existing instances while ensuring the public
        gallery can only instantiate the current typed-edge graph.
        """
        from src.domain.workflow.sql_workflow_service import SqlWorkflowService
        from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline

        ensure_public_business_node_baseline()
        allowed = {"brief", "constraint", "structured_generate", "model_router", "variants", "select_rank", "review", "workbench_task", "package_export"}
        created: list[str] = []
        workflows = SqlWorkflowService(self._factory)
        for name, graph in BENCHMARK_TEMPLATE_GRAPHS.items():
            if any(str(node.get("type")) not in allowed for node in graph["nodes"]):
                raise ValidationError_("Benchmark template contains a non-public node")
            content_hash = BENCHMARK_TEMPLATE_CONTENT_HASHES[name]
            manifest_version = f"benchmark-{content_hash}"
            with self._factory() as session:
                rows = session.execute(
                    select(WorkflowTemplateModel, WorkflowRevisionModel)
                    .join(WorkflowRevisionModel, WorkflowTemplateModel.workflow_revision_id == WorkflowRevisionModel.revision_id)
                    .where(
                        WorkflowTemplateModel.owner_scope == owner_scope.scoped_id,
                        WorkflowTemplateModel.name == name,
                        WorkflowTemplateModel.revision_status == RevisionStatus.ACTIVE,
                    )
                    .order_by(WorkflowTemplateModel.created_at.desc())
                ).all()
            current = next(
                (
                    template
                    for template, revision in rows
                    if revision.graph_hash == compute_draft_hashes(graph, {}, {})[0]
                    and str((template.manifest or {}).get("version", "")) == manifest_version
                ),
                None,
            )
            if current is not None:
                created.append(str(current.template_id))
                continue

            # Source revisions and packages are immutable.  Retire every
            # stale active package with this official name before publishing
            # the replacement so list/instantiate cannot select old content.
            if rows:
                with self._factory.begin() as session:
                    stale_ids = [template.template_id for template, _ in rows]
                    stale_rows = session.scalars(
                        select(WorkflowTemplateModel)
                        .where(WorkflowTemplateModel.template_id.in_(stale_ids))
                        .with_for_update()
                    ).all()
                    now = datetime.now(timezone.utc)
                    for stale in stale_rows:
                        stale.revision_status = RevisionStatus.RETIRED
                        stale.updated_at = now
            workflow = workflows.create_workflow(owner_scope=owner_scope)
            draft = workflows.get_draft(workflow.workflow_id)
            workflows.save_draft(workflow.workflow_id, graph, {}, {}, draft.graph_hash)
            revision = workflows.create_revision_from_draft(workflow.workflow_id, uuid4())
            created.append(self.create_template(
                name,
                str(revision.revision_id),
                manifest=WorkflowPackageManifest(name=name, version=manifest_version),
                description="官方专业基准流程，仅使用公共业务节点与 workflow-owned WorkbenchTask",
                visibility="public",
                owner_scope=owner_scope,
            ))
        return created

    def create_template(
        self, name: str, workflow_revision_id: str = "", manifest: WorkflowPackageManifest | None = None,
        description: str = "", parameter_schema: dict[str, Any] | None = None,
        default_mapping: dict[str, Any] | None = None, visibility: str = "public", owner_scope: OwnerScope | None = None,
    ) -> str:
        if not name or not workflow_revision_id:
            raise ConflictError("模板必须引用固定 WorkflowRevision")
        package = manifest or WorkflowPackageManifest(name=name)
        errors = self.validate_manifest(package)
        if errors:
            raise ConflictError(f"模板清单校验失败: {'; '.join(errors)}")
        raw_manifest = package.to_dict()
        if _contains_forbidden(raw_manifest) or _contains_forbidden(default_mapping or {}):
            raise PolicyBlockedError("模板包不得包含 secret 或 CredentialBinding")
        try:
            revision_id = UUID(workflow_revision_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError_(
                "模板 workflow_revision_id 必须是 UUID",
                details={"diagnostics": [{"code": "INVALID_UUID", "field": "workflow_revision_id", "value": workflow_revision_id}]},
            ) from exc
        with self._factory.begin() as session:
            revision = session.get(WorkflowRevisionModel, revision_id)
            if revision is None or revision.revision_status != RevisionStatus.ACTIVE:
                raise ConflictError("模板必须固定到可用的 WorkflowRevision")
            workflow = session.get(WorkflowModel, revision.workflow_id)
            if workflow is None:
                raise PolicyBlockedError("模板固定 WorkflowRevision 不存在 owner")
            # Service callers used by migrations/tests may omit an actor; HTTP
            # routes never do.  The only safe default is the source owner.
            if owner_scope is None:
                kind, _, raw_id = workflow.owner_scope.partition(":")
                owner_scope = OwnerScope(kind=kind, id=UUID(raw_id))
            if workflow.owner_scope != owner_scope.scoped_id:
                raise PolicyBlockedError("只有固定 WorkflowRevision 的 owner 可以维护模板")
            row = WorkflowTemplateModel(
                template_id=uuid4(), owner_scope=owner_scope.scoped_id, name=name, description=description, workflow_revision_id=revision_id,
                manifest=raw_manifest, parameter_schema=parameter_schema or package.parameter_schema,
                default_mapping=default_mapping or {}, visibility=visibility, provenance="platform",
                revision_status=RevisionStatus.ACTIVE,
            )
            session.add(row)
            session.flush()
            return str(row.template_id)

    def get_template(self, template_id: str, owner_scope: OwnerScope | None = None) -> TemplateRecord:
        with self._factory() as session:
            row = session.get(WorkflowTemplateModel, UUID(template_id))
            if row is None:
                raise NotFoundError("Template", template_id)
            if row.visibility != "public" and (owner_scope is None or row.owner_scope != owner_scope.scoped_id):
                raise NotFoundError("Template", template_id)
            return _record(row)

    def list_templates(self, owner_scope: OwnerScope | None = None) -> list[dict[str, Any]]:
        """List public packages plus the caller's own private packages.

        Visibility is a read boundary, not just a detail endpoint check: a
        private package must never leak through gallery metadata, while its
        maintainer still needs to discover and manage it.
        """
        with self._factory() as session:
            visibility = WorkflowTemplateModel.visibility == "public"
            if owner_scope is not None:
                visibility = or_(visibility, WorkflowTemplateModel.owner_scope == owner_scope.scoped_id)
            rows = session.scalars(select(WorkflowTemplateModel).where(
                visibility,
                WorkflowTemplateModel.revision_status == RevisionStatus.ACTIVE,
            ).order_by(WorkflowTemplateModel.created_at.desc())).all()
            return [{
                "template_id": str(row.template_id), "name": row.name, "description": row.description,
                "visibility": row.visibility, "provenance": row.provenance,
                "revision_status": row.revision_status.value, "parameter_schema": row.parameter_schema or {},
                "created_at": row.created_at.isoformat(),
            } for row in rows]

    def update_template(self, template_id: str, owner_scope: OwnerScope, *, name: str | None = None, description: str | None = None, visibility: str | None = None) -> TemplateRecord:
        """Owner-only metadata update; package/revision content stays immutable."""
        with self._factory.begin() as session:
            row = session.get(WorkflowTemplateModel, UUID(template_id))
            if row is None or row.owner_scope != owner_scope.scoped_id:
                raise NotFoundError("Template", template_id)
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if visibility is not None:
                row.visibility = visibility
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _record(row)

    @staticmethod
    def _diagnostic(dep: PackageDependency, code: str, message: str, *, path: list[str]) -> dict[str, Any]:
        """Stable, non-secret diagnostics suitable for the dependency UI."""
        return {
            "code": code,
            "dep_id": dep.dep_id,
            "kind": dep.kind.value,
            "revision_id": dep.revision_id,
            "message": message,
            "path": path,
        }

    @staticmethod
    def _uuid_or_diagnostic(value: str) -> UUID | None:
        try:
            return UUID(value)
        except (TypeError, ValueError):
            return None

    def _resolve_replacement(
        self,
        *,
        session: Session,
        dep: PackageDependency,
        slot: ReplacementSlot,
        value: str,
        owner_scope: OwnerScope | None,
        path: list[str],
    ) -> tuple[bool, dict[str, Any] | None]:
        """Validate a replacement against the declared typed slot.

        Provider identifiers intentionally remain opaque AtlasCloud model refs.
        Every durable local reference is a revision UUID and is re-authorized
        at instantiation time; arbitrary strings cannot smuggle private data
        into a package instance.
        """
        if slot.expected_kind == DependencyKind.PROVIDER:
            if not value.strip() or _contains_forbidden(value):
                return False, self._diagnostic(dep, "INVALID_REPLACEMENT", "Provider replacement is invalid", path=path)
            return True, None
        reference_id = self._uuid_or_diagnostic(value)
        if reference_id is None:
            return False, self._diagnostic(dep, "INVALID_UUID", f"Replacement slot {slot.slot_id} requires a revision UUID", path=path)
        synthetic = PackageDependency(dep.dep_id, slot.expected_kind, str(reference_id), schema_id=dep.schema_id)
        ok, diagnostic = self._resolve_direct_dependency(session, synthetic, owner_scope, path)
        return ok, diagnostic

    def _resolve_direct_dependency(
        self, session: Session, dep: PackageDependency, owner_scope: OwnerScope | None, path: list[str]
    ) -> tuple[bool, dict[str, Any] | None]:
        """Validate one non-template dependency, including its entitlement."""
        revision_id = self._uuid_or_diagnostic(dep.revision_id)
        if dep.kind == DependencyKind.PROVIDER:
            if _contains_forbidden(dep.revision_id):
                return False, self._diagnostic(dep, "FORBIDDEN_PROVIDER_REFERENCE", "Provider reference contains forbidden content", path=path)
            return True, None
        if revision_id is None:
            return False, self._diagnostic(dep, "INVALID_UUID", "Dependency revision_id must be a UUID", path=path)
        if dep.kind == DependencyKind.RESOURCE:
            revision = session.get(ResourceRevisionModel, revision_id)
            resource = session.get(ResourceModel, revision.resource_id) if revision else None
            permitted = resource is not None and owner_scope is not None and resource.owner_scope == owner_scope.scoped_id
            if not permitted and revision is not None and owner_scope is not None:
                permitted = session.scalar(select(ResourceGrantSnapshotModel.grant_snapshot_id).where(
                    ResourceGrantSnapshotModel.resource_revision_id == revision.revision_id,
                    ResourceGrantSnapshotModel.grantee_scope == owner_scope.scoped_id,
                    ResourceGrantSnapshotModel.status == "active",
                )) is not None
            if revision is None or resource is None:
                return False, self._diagnostic(dep, "MISSING_DEPENDENCY", "Resource revision does not exist", path=path)
            if not permitted:
                return False, self._diagnostic(dep, "ENTITLEMENT_DENIED", "Resource revision is not granted to this owner", path=path)
            return True, None
        if dep.kind == DependencyKind.WORKFLOW:
            revision = session.get(WorkflowRevisionModel, revision_id)
            if revision is None or revision.revision_status != RevisionStatus.ACTIVE:
                return False, self._diagnostic(dep, "MISSING_DEPENDENCY", "Workflow revision is unavailable", path=path)
            return True, None
        if dep.kind == DependencyKind.NODE_DEFINITION:
            definition = session.get(NodeDefinitionModel, revision_id)
            if definition is None or definition.status.value != "active":
                return False, self._diagnostic(dep, "MISSING_DEPENDENCY", "Node definition is unavailable", path=path)
            if dep.schema_id and str((definition.body or {}).get("schema_id", "")) != dep.schema_id:
                return False, self._diagnostic(dep, "SCHEMA_MISMATCH", "Node definition schema does not match manifest", path=path)
            return True, None
        if dep.kind == DependencyKind.CONVERTER:
            if session.get(ConverterRevisionModel, revision_id) is None:
                return False, self._diagnostic(dep, "MISSING_DEPENDENCY", "Converter revision is unavailable", path=path)
            return True, None
        if dep.kind == DependencyKind.AGENT:
            revision = session.get(AgentRevisionModel, revision_id)
            definition = session.get(AgentDefinitionModel, revision.agent_id) if revision else None
            ok = revision is not None and definition is not None and owner_scope is not None and definition.owner_scope == owner_scope.scoped_id and revision.status == "active"
        elif dep.kind == DependencyKind.MEDIA_RECIPE:
            revision = session.get(MediaRecipeRevisionModel, revision_id)
            definition = session.get(MediaRecipeDefinitionModel, revision.recipe_id) if revision else None
            ok = revision is not None and definition is not None and owner_scope is not None and definition.owner_scope == owner_scope.scoped_id and revision.status == "active"
        elif dep.kind == DependencyKind.SKILL:
            revision = session.get(SkillRevisionModel, revision_id)
            definition = session.get(SkillContentModel, revision.skill_id) if revision else None
            ok = revision is not None and definition is not None and owner_scope is not None and definition.owner_scope == owner_scope.scoped_id and revision.status == "active"
        else:
            return False, self._diagnostic(dep, "UNSUPPORTED_DEPENDENCY", "Dependency kind is not package-resolvable", path=path)
        if not ok:
            return False, self._diagnostic(dep, "ENTITLEMENT_DENIED", "Dependency is unavailable or not owned by this owner", path=path)
        return True, None

    def resolve_import_manifest(
        self,
        manifest: WorkflowPackageManifest,
        *,
        replacements: dict[str, str] | None,
        owner_scope: OwnerScope,
    ) -> dict[str, Any]:
        """Resolve an untrusted package before it is allowed into a Draft.

        This deliberately shares the durable dependency/entitlement checks
        used by template instantiation, but does not create a Template or a
        Revision.  An imported package is still untrusted after this check;
        the result only proves that its declared dependency closure is
        currently selectable by the importing owner.
        """
        errors = self.validate_manifest(manifest)
        if errors:
            raise ValidationError_("WorkflowPackageManifest is invalid", details={"errors": errors})
        if manifest.version != "1.0.0":
            raise ValidationError_(
                "Unsupported WorkflowPackageManifest version",
                details={"diagnostics": [{"code": "UNSUPPORTED_PACKAGE_VERSION", "version": manifest.version}]},
            )
        raw = manifest.to_dict()
        replacements = replacements or {}
        if _contains_forbidden(raw) or _contains_forbidden(replacements):
            raise PolicyBlockedError("导入包不得包含 secret 或 CredentialBinding")

        slot_by_id = {slot.slot_id: slot for slot in manifest.replacement_slots}
        missing: list[str] = []
        unresolved_slots: list[str] = []
        resolution: dict[str, str] = {}
        diagnostics: list[dict[str, Any]] = []
        closure: list[dict[str, Any]] = []
        with self._factory() as session:
            for dep in manifest.dependencies:
                path = ["import", dep.dep_id]
                if dep.replacement_slot:
                    slot = slot_by_id.get(dep.replacement_slot)
                    replacement = replacements.get(dep.replacement_slot)
                    if slot is None:
                        missing.append(dep.dep_id)
                        diagnostics.append(self._diagnostic(dep, "INVALID_SLOT", "Dependency names a missing replacement slot", path=path))
                    elif not replacement and slot.required:
                        unresolved_slots.append(slot.slot_id)
                        diagnostics.append(self._diagnostic(dep, "REPLACEMENT_REQUIRED", f"Replacement slot {slot.slot_id} is required", path=path))
                    elif replacement:
                        valid, diagnostic = self._resolve_replacement(
                            session=session, dep=dep, slot=slot, value=replacement,
                            owner_scope=owner_scope, path=path,
                        )
                        if valid:
                            resolution[dep.dep_id] = replacement
                            closure.append({"path": path, "dep_id": dep.dep_id, "kind": slot.expected_kind.value, "revision_id": replacement, "source": "replacement"})
                        else:
                            missing.append(dep.dep_id)
                            assert diagnostic is not None
                            diagnostics.append(diagnostic)
                    continue
                if dep.kind == DependencyKind.TEMPLATE:
                    nested = self._resolve_import_template_dependency(session, dep, owner_scope, path, set(), replacements)
                    closure.extend(nested["closure"])
                    if not nested["resolved"]:
                        missing.append(dep.dep_id)
                        diagnostics.extend(nested["diagnostics"])
                    else:
                        resolution[dep.dep_id] = dep.revision_id
                        closure.append({"path": path, "dep_id": dep.dep_id, "kind": dep.kind.value, "revision_id": dep.revision_id, "source": "nested_template"})
                    continue
                valid, diagnostic = self._resolve_direct_dependency(session, dep, owner_scope, path)
                if valid:
                    # Import packages may select only the platform's provider
                    # namespace.  Older local templates retain their legacy
                    # compatibility rule; this is an external-input boundary.
                    if dep.kind == DependencyKind.PROVIDER and not dep.revision_id.startswith("atlascloud/"):
                        valid = False
                        diagnostic = self._diagnostic(dep, "UNSUPPORTED_PROVIDER", "Imported packages must use an AtlasCloud provider reference", path=path)
                if valid:
                    resolution[dep.dep_id] = dep.revision_id
                    closure.append({"path": path, "dep_id": dep.dep_id, "kind": dep.kind.value, "revision_id": dep.revision_id, "source": "manifest"})
                else:
                    missing.append(dep.dep_id)
                    assert diagnostic is not None
                    diagnostics.append(diagnostic)
        return {
            "resolved": not missing and not unresolved_slots,
            "missing": list(dict.fromkeys(missing)),
            "unresolved_slots": list(dict.fromkeys(unresolved_slots)),
            "available": not missing,
            "resolution": resolution,
            "diagnostics": diagnostics,
            "closure": closure,
        }

    def _resolve_import_template_dependency(
        self, session: Session, dep: PackageDependency, owner_scope: OwnerScope, path: list[str], seen: set[str],
        replacements: dict[str, str],
    ) -> dict[str, Any]:
        """Resolve nested template closure without hiding private/missing IDs."""
        template_uuid = self._uuid_or_diagnostic(dep.revision_id)
        if template_uuid is None:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "INVALID_UUID", "Template dependency id must be a UUID", path=path)], "closure": []}
        key = str(template_uuid)
        if key in seen:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "TEMPLATE_CYCLE", "Template dependency cycle detected", path=[*path, key])], "closure": []}
        row = session.get(WorkflowTemplateModel, template_uuid)
        if row is None:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "MISSING_DEPENDENCY", "Nested template does not exist", path=path)], "closure": []}
        if row.visibility != "public" and row.owner_scope != owner_scope.scoped_id:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "ENTITLEMENT_DENIED", "Nested template is private or unavailable", path=path)], "closure": []}
        if row.revision_status != RevisionStatus.ACTIVE:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "MISSING_DEPENDENCY", "Nested template revision is unavailable", path=path)], "closure": []}
        next_seen = {*seen, key}
        closure: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        nested_manifest = _manifest_from_json(row.manifest or {})
        if _contains_forbidden(row.manifest or {}):
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "PACKAGE_FORBIDDEN_CONTENT", "Nested template contains forbidden package content", path=path)], "closure": []}
        errors = self.validate_manifest(nested_manifest)
        if errors:
            return {"resolved": False, "diagnostics": [self._diagnostic(dep, "INVALID_MANIFEST", "Nested template manifest is invalid", path=path)], "closure": []}
        slots = {slot.slot_id: slot for slot in nested_manifest.replacement_slots}
        for nested_dep in nested_manifest.dependencies:
            nested_path = [*path, key, nested_dep.dep_id]
            if nested_dep.replacement_slot:
                slot = slots.get(nested_dep.replacement_slot)
                replacement = replacements.get(nested_dep.replacement_slot)
                if slot is None:
                    diagnostics.append(self._diagnostic(nested_dep, "INVALID_SLOT", "Dependency names a missing replacement slot", path=nested_path))
                elif not replacement and slot.required:
                    diagnostics.append(self._diagnostic(nested_dep, "REPLACEMENT_REQUIRED", f"Replacement slot {slot.slot_id} is required", path=nested_path))
                elif replacement:
                    valid, diagnostic = self._resolve_replacement(
                        session=session, dep=nested_dep, slot=slot, value=replacement,
                        owner_scope=owner_scope, path=nested_path,
                    )
                    if valid:
                        closure.append({"path": nested_path, "dep_id": nested_dep.dep_id, "kind": slot.expected_kind.value, "revision_id": replacement, "source": "replacement"})
                    else:
                        assert diagnostic is not None
                        diagnostics.append(diagnostic)
                continue
            if nested_dep.kind == DependencyKind.TEMPLATE:
                result = self._resolve_import_template_dependency(session, nested_dep, owner_scope, nested_path, next_seen, replacements)
                closure.extend(result["closure"])
                diagnostics.extend(result["diagnostics"])
            else:
                valid, diagnostic = self._resolve_direct_dependency(session, nested_dep, owner_scope, nested_path)
                if valid and nested_dep.kind == DependencyKind.PROVIDER and not nested_dep.revision_id.startswith("atlascloud/"):
                    valid = False
                    diagnostic = self._diagnostic(nested_dep, "UNSUPPORTED_PROVIDER", "Imported packages must use an AtlasCloud provider reference", path=nested_path)
                if valid:
                    closure.append({"path": nested_path, "dep_id": nested_dep.dep_id, "kind": nested_dep.kind.value, "revision_id": nested_dep.revision_id, "source": "nested_manifest"})
                else:
                    assert diagnostic is not None
                    diagnostics.append(diagnostic)
        return {"resolved": not diagnostics, "diagnostics": diagnostics, "closure": closure}

    def resolve_dependencies(self, template_id: str, replacements: dict[str, str] | None = None, owner_scope: OwnerScope | None = None, _seen: set[str] | None = None, _path: list[str] | None = None) -> dict[str, Any]:
        """Resolve every package dependency against the current durable state.

        A package never gets the old V0 treatment of assuming an arbitrary
        revision exists.  The currently supported package dependency that has
        a local executable target is ``workflow``; other non-slotted kinds
        remain fixed references and are checked by their downstream compiler.
        A grant-required reference is deliberately unavailable until the
        entitlement service supplies its GrantSnapshot.
        """
        seen = _seen or set()
        path = list(_path or [])
        if template_id in seen:
            diagnostic = {"code": "TEMPLATE_CYCLE", "dep_id": template_id, "kind": DependencyKind.TEMPLATE.value, "revision_id": template_id, "message": "Template dependency cycle detected", "path": [*path, template_id]}
            return {"resolved": False, "missing": [template_id], "unresolved_slots": [], "available": False, "resolution": {}, "diagnostics": [diagnostic], "closure": []}
        seen = { *seen, template_id }
        try:
            template = self.get_template(template_id, owner_scope)
        except ValueError:
            diagnostic = {"code": "INVALID_UUID", "dep_id": template_id, "kind": DependencyKind.TEMPLATE.value, "revision_id": template_id, "message": "Template id must be a UUID", "path": [*path, template_id]}
            return {"resolved": False, "missing": [template_id], "unresolved_slots": [], "available": False, "resolution": {}, "diagnostics": [diagnostic], "closure": []}
        replacements = replacements or {}
        unresolved_slots: list[str] = []
        missing: list[str] = []
        resolution: dict[str, str] = {}
        diagnostics: list[dict[str, Any]] = []
        closure: list[dict[str, Any]] = []
        slot_by_id = {slot.slot_id: slot for slot in template.manifest.replacement_slots}
        with self._factory() as session:
            for dep in template.manifest.dependencies:
                dep_path = [*path, template_id, dep.dep_id]
                if dep.replacement_slot:
                    slot = slot_by_id.get(dep.replacement_slot)
                    replacement = replacements.get(dep.replacement_slot)
                    if slot is None:
                        missing.append(dep.dep_id)
                        diagnostics.append(self._diagnostic(dep, "INVALID_SLOT", "Dependency names a missing replacement slot", path=dep_path))
                    elif not replacement and slot.required:
                        unresolved_slots.append(slot.slot_id)
                        diagnostics.append(self._diagnostic(dep, "REPLACEMENT_REQUIRED", f"Replacement slot {slot.slot_id} is required", path=dep_path))
                    elif replacement:
                        valid, diagnostic = self._resolve_replacement(session=session, dep=dep, slot=slot, value=replacement, owner_scope=owner_scope, path=dep_path)
                        if valid:
                            resolution[dep.dep_id] = replacement
                            closure.append({"path": dep_path, "dep_id": dep.dep_id, "kind": slot.expected_kind.value, "revision_id": replacement, "source": "replacement"})
                        else:
                            missing.append(dep.dep_id)
                            assert diagnostic is not None
                            diagnostics.append(diagnostic)
                    continue
                if dep.kind == DependencyKind.TEMPLATE:
                    try:
                        nested = self.resolve_dependencies(dep.revision_id, replacements, owner_scope, seen, dep_path)
                    except (NotFoundError, ValueError):
                        nested = {"resolved": False, "missing": [dep.dep_id], "diagnostics": [self._diagnostic(dep, "MISSING_DEPENDENCY", "Nested template is unavailable", path=dep_path)], "closure": []}
                    closure.extend(nested.get("closure", []))
                    if not nested["resolved"]:
                        missing.append(dep.dep_id)
                        missing.extend(f"{dep.dep_id}/{item}" for item in nested["missing"])
                        diagnostics.extend(nested.get("diagnostics", []))
                        continue
                    resolution[dep.dep_id] = dep.revision_id
                    closure.append({"path": dep_path, "dep_id": dep.dep_id, "kind": dep.kind.value, "revision_id": dep.revision_id, "source": "nested_template"})
                    continue
                valid, diagnostic = self._resolve_direct_dependency(session, dep, owner_scope, dep_path)
                if not valid:
                    missing.append(dep.dep_id)
                    assert diagnostic is not None
                    diagnostics.append(diagnostic)
                    continue
                if dep.grant_required and dep.kind != DependencyKind.RESOURCE:
                    missing.append(dep.dep_id)
                    diagnostics.append(self._diagnostic(dep, "GRANT_REQUIRED", "Dependency requires an entitlement grant", path=dep_path))
                    continue
                resolution[dep.dep_id] = dep.revision_id
                closure.append({"path": dep_path, "dep_id": dep.dep_id, "kind": dep.kind.value, "revision_id": dep.revision_id, "source": "manifest"})
        return {
            "resolved": not missing and not unresolved_slots,
            "missing": list(dict.fromkeys(missing)),
            "unresolved_slots": unresolved_slots,
            "available": not missing,
            "resolution": resolution,
            "diagnostics": diagnostics,
            "closure": closure,
        }

    def instantiate_template(
        self, template_id: str, owner_scope: OwnerScope, project_name: str = "", project_description: str = "",
        parameters: dict[str, Any] | None = None, replacements: dict[str, str] | None = None,
    ) -> InstanceRecord:
        parameters, replacements = parameters or {}, replacements or {}
        with self._factory.begin() as session:
            row = session.get(WorkflowTemplateModel, UUID(template_id))
            if row is None or row.revision_status != RevisionStatus.ACTIVE:
                raise NotFoundError("Template", template_id)
            if row.visibility != "public" and row.owner_scope != owner_scope.scoped_id:
                raise NotFoundError("Template", template_id)
            template = _record(row)
            errors = self.validate_manifest(template.manifest)
            if errors:
                raise ConflictError(f"模板清单校验失败: {'; '.join(errors)}")
            resolution = self.resolve_dependencies(template_id, replacements, owner_scope)
            if not resolution["resolved"]:
                raise ConflictError("模板依赖未满足，无法实例化", details={
                    "missing_deps": resolution["missing"], "unresolved_slots": resolution["unresolved_slots"],
                })
            source = session.get(WorkflowRevisionModel, row.workflow_revision_id)
            if source is None or source.revision_status != RevisionStatus.ACTIVE:
                raise ConflictError("模板引用的 WorkflowRevision 不可用")
            # Instantiation is not a blind graph clone.  Dependencies can be
            # revoked and node/provider policy can change after publication,
            # so re-check the exact mapped draft immediately before durable
            # rows are created.  No failed preflight leaves a project, draft,
            # or instance record behind because this runs inside this txn.
            config = deepcopy(source.config or {})
            config["template_parameters"] = deepcopy(parameters)
            config["template_dependency_mapping"] = dict(resolution["resolution"])
            graph, layout = deepcopy(source.graph or {}), deepcopy(source.layout or {})
            self._preflight_instance_graph(
                graph=graph,
                config=config,
                owner_scope=owner_scope,
                dependency_resolution=resolution,
            )
            now = datetime.now(timezone.utc)
            project = ProjectModel(project_id=uuid4(), owner_scope=owner_scope.scoped_id,
                name=project_name or f"from_{template.name}", description=project_description or template.description,
                status=ProjectStatus.ACTIVE, default_entry="canvas", created_at=now, updated_at=now)
            workflow = WorkflowModel(workflow_id=uuid4(), owner_scope=owner_scope.scoped_id, created_at=now)
            session.add_all([project, workflow])
            session.flush()
            graph_hash, layout_hash, execution_hash = compute_draft_hashes(graph, config, layout)
            session.add(WorkflowDraftModel(workflow_id=workflow.workflow_id, draft_version=1,
                base_revision_id=source.revision_id, graph=graph, config=config, layout=layout,
                graph_hash=graph_hash, layout_hash=layout_hash, execution_hash=execution_hash, updated_at=now))
            attribution = {
                "template_id": str(row.template_id), "template_name": row.name,
                "template_revision_id": str(source.revision_id), "provenance": row.provenance,
                "parameter_snapshot": parameters, "dependency_resolution": resolution["resolution"],
                "replacement_mapping": replacements,
            }
            instance = WorkflowTemplateInstanceModel(instance_id=uuid4(), template_id=row.template_id,
                template_revision_id=source.revision_id, project_id=project.project_id, workflow_id=workflow.workflow_id,
                dependency_resolution=resolution["resolution"], replacement_mapping=replacements,
                attribution_manifest=attribution, created_at=now)
            session.add(instance)
            session.flush()
            return _instance(instance)

    def _preflight_instance_graph(
        self,
        *,
        graph: dict[str, Any],
        config: dict[str, Any],
        owner_scope: OwnerScope,
        dependency_resolution: dict[str, Any],
    ) -> None:
        """Run current policy/capability/compiler gates before a clone exists.

        The template Revision remains immutable, while every instance is
        checked against today's public registry and the caller's resolved
        entitlement closure.  Provider refs are deliberately AtlasCloud-only;
        credentials are never accepted in a package payload.
        """
        if _contains_forbidden(graph) or _contains_forbidden(config):
            raise PolicyBlockedError("模板实例图包含 secret、凭证或不安全命令")
        if not dependency_resolution.get("resolved"):
            raise ConflictError("模板依赖未满足，无法实例化")
        for node in graph.get("nodes", []):
            if not isinstance(node, dict):
                raise ValidationError_("模板图节点必须是对象")
            node_config = node.get("config", {})
            if not isinstance(node_config, dict):
                raise ValidationError_("模板图节点配置必须是对象")
            if str(node.get("type", "")) == "model_router":
                policy = str(node_config.get("provider_selection_policy_ref", ""))
                models = node_config.get("enabled_models", [])
                if (
                    not policy.startswith("atlascloud.")
                    or not isinstance(models, list)
                    or not models
                    or any(not isinstance(model, str) or not model.startswith("atlascloud/") for model in models)
                ):
                    raise PolicyBlockedError("模板 Model Router 必须固定为已启用的 AtlasCloud policy/model")
        # Compiler snapshots are persisted registry data, never a browser
        # catalog.  Ensure the public baseline is present for a clean deploy.
        from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline

        ensure_public_business_node_baseline()
        # Do not select an arbitrary historical snapshot: it may predate a
        # newly activated public definition (for example human_gate). Freeze
        # the exact current approved registry for this preflight instead.
        snapshot = SqlRegistryService(self._factory).freeze_snapshot()
        try:
            WorkflowCompiler().compile(
                workflow_revision_id=uuid4(), graph=graph, registry_snapshot=snapshot
            )
        except CompilationError as exc:
            raise ConflictError("模板实例预检编译失败", details=exc.to_dict()) from exc

    def get_instance(self, instance_id: str, owner_scope: OwnerScope | None = None) -> InstanceRecord:
        with self._factory() as session:
            row = session.get(WorkflowTemplateInstanceModel, UUID(instance_id))
            if row is None:
                raise NotFoundError("TemplateInstance", instance_id)
            project = session.get(ProjectModel, row.project_id)
            # HTTP always supplies the actor.  Keep direct repository reads
            # available for maintenance/restart verification only.
            if project is None or (owner_scope is not None and project.owner_scope != owner_scope.scoped_id):
                raise NotFoundError("TemplateInstance", instance_id)
            return _instance(row)

    def list_instances_by_project(self, project_id: str) -> list[InstanceRecord]:
        with self._factory() as session:
            rows = session.scalars(select(WorkflowTemplateInstanceModel).where(
                WorkflowTemplateInstanceModel.project_id == UUID(project_id)
            )).all()
            return [_instance(row) for row in rows]
