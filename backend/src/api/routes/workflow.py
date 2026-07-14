"""
ToonFlow Backend — API Routes for Workflow
"""
from __future__ import annotations

from typing import Any
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.domain.workflow.compiler import WorkflowCompiler, CompilationError
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.registry_repository import SqlRegistryService
from src.schemas.models import NodeDefinitionRevision, PortTypeRef, RegistrySnapshot
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError_
from src.infra.db.identity_repository import get_session_store
from src.schemas.models import OwnerScope
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.models import (
    ArtifactVersionModel,
    MediaRecipeDefinitionModel,
    MediaRecipeRevisionModel,
    ResourceGrantSnapshotModel,
    ResourceModel,
    ResourceRevisionModel,
)
from src.infra.db.session import get_session_factory

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])

# API lifecycle uses the durable PostgreSQL implementation.  The in-memory
# WorkflowService and RegistryService remain focused unit-test doubles only.
_workflow_service = SqlWorkflowService()
_registry_service = SqlRegistryService()
_compiler = WorkflowCompiler()
_sessions = get_session_store()
_agents = SqlAgentRepository()


class CreateWorkflowRequest(BaseModel):
    """Ownership is always inferred from the authenticated bearer."""


class SaveDraftRequest(BaseModel):
    graph: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    layout: dict[str, Any] = Field(default_factory=dict)
    base_graph_hash: str
    pinned_dependency_revisions: list[str] = Field(default_factory=list)


class RollbackDraftRequest(BaseModel):
    """Explicit confirmation that the owner reviewed the target revision."""

    base_graph_hash: str = Field(min_length=1)
    confirm_revision_id: uuid.UUID


def _resolve_owner(authorization: str | None) -> OwnerScope:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    try:
        account_id = _sessions.account_for_token(parts[1])
    except (NotFoundError, ConflictError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_dict()) from exc
    return OwnerScope(kind="user", id=account_id)


def _failed_diagnostic(message: str, location: str = "registry") -> dict[str, Any]:
    return {"status": "failed", "diagnostics": [{"severity": "error", "location": location, "message": message}]}


def _assert_graph_reference_authorization(graph: dict[str, Any], owner: OwnerScope) -> None:
    """Resolve literal graph refs against the current owner before compile.

    ArtifactVersion is never cross-owner consumable.  A foreign resource must
    carry an active, matching grant snapshot; this makes an imported graph
    fail closed before its draft can be published or run.
    """
    candidates: list[dict[str, Any]] = []
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "artifact_version_id" in value or "resource_revision_id" in value:
                candidates.append(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
    visit(graph)
    if not candidates:
        return
    with get_session_factory()() as session:
        for ref in candidates:
            if ref.get("artifact_version_id"):
                try:
                    artifact_id = uuid.UUID(str(ref["artifact_version_id"]))
                except (TypeError, ValueError) as exc:
                    raise ValidationError_("ArtifactRef version id is invalid") from exc
                artifact = session.get(ArtifactVersionModel, artifact_id)
                if artifact is None or artifact.owner_scope != owner.scoped_id:
                    raise ForbiddenError("跨 owner ArtifactRef 被拒绝；请先提升为已授权 ResourceRevision")
            if ref.get("resource_revision_id"):
                try:
                    revision_id = uuid.UUID(str(ref["resource_revision_id"]))
                except (TypeError, ValueError) as exc:
                    raise ValidationError_("ResourceRef revision id is invalid") from exc
                revision = session.get(ResourceRevisionModel, revision_id)
                resource = session.get(ResourceModel, revision.resource_id) if revision else None
                if revision is None or resource is None:
                    raise NotFoundError("ResourceRevision", str(revision_id))
                if resource.owner_scope == owner.scoped_id:
                    continue
                raw_grant = ref.get("grant_snapshot_id")
                try:
                    grant_id = uuid.UUID(str(raw_grant))
                except (TypeError, ValueError) as exc:
                    raise ForbiddenError("跨 owner ResourceRef 必须携带有效 GrantSnapshot") from exc
                grant = session.get(ResourceGrantSnapshotModel, grant_id)
                if (
                    grant is None
                    or grant.resource_revision_id != revision_id
                    or grant.grantee_scope != owner.scoped_id
                    or grant.status != "active"
                ):
                    raise ForbiddenError("跨 owner ResourceRef 授权无效")


def _agent_port(schema_ref: str, port_id: str) -> PortTypeRef:
    schema_id, marker, version = schema_ref.rpartition(".v")
    if not marker or not schema_id or not version.isdigit():
        schema_id, version = "toonflow.agent_output", "1"
    return PortTypeRef(port_id=port_id, type_id="artifact", schema_id=schema_id,
                       schema_version=int(version), cardinality="required")


def _agent_definitions_for_graph(graph: dict[str, Any], owner: OwnerScope) -> list[NodeDefinitionRevision]:
    """Resolve only active, owner-owned pinned Agent revisions in a Draft."""
    definitions: list[NodeDefinitionRevision] = []
    for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict) or not str(node.get("type", "")).startswith("agent.invoke."):
            continue
        node_id, node_type = str(node.get("id", "")), str(node["type"])
        config = node.get("config") if isinstance(node.get("config"), dict) else (node.get("data") or {}).get("config", {})
        config = config if isinstance(config, dict) else {}
        raw_revision = config.get("agent_revision_id")
        if not raw_revision or node_type != f"agent.invoke.{raw_revision}":
            raise ValidationError_("Agent canvas node must pin a matching agent_revision_id", details={"field": f"node:{node_id}"})
        try:
            revision_id = uuid.UUID(str(raw_revision))
        except (TypeError, ValueError) as exc:
            raise ValidationError_("Agent canvas revision id is invalid", details={"field": f"node:{node_id}"}) from exc
        revision = _agents.get_revision(revision_id)
        definition = _agents.get_definition_for_revision(revision_id)
        if definition.owner_scope != owner.scoped_id:
            raise ForbiddenError("Agent canvas node belongs to another owner_scope")
        if revision.revision_status.value != "active":
            raise ValidationError_("Agent canvas node requires an active frozen revision", details={"field": f"node:{node_id}"})
        body = revision.model_dump(mode="json")
        definitions.append(NodeDefinitionRevision(
            node_type_id=node_type, revision_id=revision_id,
            semantic_version=f"agent-r{revision.revision_number}", executor_ref="agent_invoke",
            input_ports=[_agent_port(str(body.get("input_schema_ref") or "toonflow.agent_input.v1"), "input")],
            output_ports=[_agent_port(str(body.get("output_schema_ref") or "toonflow.agent_output.v1"), "output")],
            config_schema={"type": "object", "properties": {"agent_revision_id": {"const": str(revision_id)}}, "required": ["agent_revision_id"]},
            policy_metadata={"owner_scope": owner.scoped_id, "agent_revision_id": str(revision_id)},
            ui_metadata={"label": definition.name},
        ))
    return definitions


def _recipe_port(schema_ref: str, port_id: str) -> PortTypeRef:
    schema_id, marker, version = schema_ref.rpartition(".v")
    if not marker or not schema_id or not version.isdigit():
        schema_id, version = "toonflow.media_output", "1"
    return PortTypeRef(port_id=port_id, type_id="artifact", schema_id=schema_id,
                       schema_version=int(version), cardinality="optional")


def _recipe_definitions_for_graph(graph: dict[str, Any], owner: OwnerScope) -> list[NodeDefinitionRevision]:
    """Resolve active owner-owned Recipe revisions as one frozen outer node.

    Recipe operators are deliberately not registry entries here.  The outer
    dynamic definition is the only canvas-facing surface; its config pins a
    single immutable MediaRecipeRevision which the worker expands privately.
    """
    definitions: list[NodeDefinitionRevision] = []
    for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
        if not isinstance(node, dict) or not str(node.get("type", "")).startswith("media.recipe."):
            continue
        node_id, node_type = str(node.get("id", "")), str(node["type"])
        config = node.get("config") if isinstance(node.get("config"), dict) else (node.get("data") or {}).get("config", {})
        config = config if isinstance(config, dict) else {}
        raw_revision = config.get("media_recipe_revision_id")
        if not raw_revision or node_type != f"media.recipe.{raw_revision}":
            raise ValidationError_("MediaRecipe canvas node must pin a matching media_recipe_revision_id", details={"field": f"node:{node_id}"})
        try:
            revision_id = uuid.UUID(str(raw_revision))
        except (TypeError, ValueError) as exc:
            raise ValidationError_("MediaRecipe canvas revision id is invalid", details={"field": f"node:{node_id}"}) from exc
        with get_session_factory()() as session:
            revision = session.get(MediaRecipeRevisionModel, revision_id)
            definition = session.get(MediaRecipeDefinitionModel, revision.recipe_id) if revision else None
            if revision is None or definition is None:
                raise NotFoundError("MediaRecipeRevision", str(revision_id))
            if definition.owner_scope != owner.scoped_id:
                raise ForbiddenError("MediaRecipe canvas node belongs to another owner_scope")
            if revision.status != "active":
                raise ValidationError_("MediaRecipe canvas node requires an active frozen revision", details={"field": f"node:{node_id}"})
            body = dict(revision.body or {})
        inputs = [str(value) for value in body.get("public_input_schema_refs", []) if isinstance(value, str)]
        outputs = [str(value) for value in body.get("public_output_schema_refs", []) if isinstance(value, str)]
        definitions.append(NodeDefinitionRevision(
            node_type_id=node_type, revision_id=revision_id, semantic_version=f"recipe-r{revision.revision_number}",
            executor_ref="workflow.business.media_recipe_invoke",
            input_ports=[_recipe_port(value, f"input_{index}") for index, value in enumerate(inputs)],
            output_ports=[_recipe_port(value, f"output_{index}") for index, value in enumerate(outputs)],
            config_schema={"type": "object", "properties": {"media_recipe_revision_id": {"const": str(revision_id)}}, "required": ["media_recipe_revision_id"]},
            policy_metadata={"owner_scope": owner.scoped_id, "media_recipe_revision_id": str(revision_id), "provider": "atlascloud"},
            ui_metadata={"label": definition.name, "category": "Media Recipes"},
        ))
    return definitions


def _snapshot_for_graph(graph: dict[str, Any], owner: OwnerScope, *, persist: bool) -> RegistrySnapshot:
    extras = [*_agent_definitions_for_graph(graph, owner), *_recipe_definitions_for_graph(graph, owner)]
    if persist:
        return _registry_service.create_snapshot(extras)[0]
    snapshots = _registry_service.list_snapshots()
    if not snapshots:
        raise NotFoundError("RegistrySnapshot", "active baseline")
    base = snapshots[0]
    return base.model_copy(update={"node_definitions": {**base.node_definitions, **{item.node_type_id: item for item in extras}}})


def _registry_for_revision(revision_id: uuid.UUID | None) -> RegistrySnapshot | None:
    """Select the pinned snapshot for a revision, or latest snapshot for a draft."""
    if revision_id is not None:
        revision = _workflow_service.get_revision(revision_id)
        try:
            return _registry_service.get_snapshot(revision.registry_snapshot_id)
        except NotFoundError:
            return None
    snapshots = _registry_service.list_snapshots()
    return snapshots[0] if snapshots else None


@router.get("/")
async def list_workflows(authorization: str | None = Header(None)):
    """List all workflows for the current owner."""
    owner = _resolve_owner(authorization)
    return {"workflows": [{"workflow_id": str(item.workflow_id)} for item in _workflow_service.list_workflows(owner_scope=owner)]}


@router.post("/", status_code=201)
async def create_workflow(body: CreateWorkflowRequest, authorization: str | None = Header(None)):
    owner = _resolve_owner(authorization)
    workflow = _workflow_service.create_workflow(owner_scope=owner)
    return {"workflow_id": str(workflow.workflow_id), "owner_scope": workflow.owner_scope.scoped_id}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: uuid.UUID, authorization: str | None = Header(None)):
    """Get a specific workflow."""
    wf = _workflow_service.get_workflow(workflow_id)
    if not wf or wf.owner_scope.scoped_id != _resolve_owner(authorization).scoped_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow_id": str(wf.workflow_id)}


@router.get("/{workflow_id}/draft")
async def get_draft(workflow_id: uuid.UUID, authorization: str | None = Header(None)):
    """Get the current draft of a workflow."""
    owner = _resolve_owner(authorization)
    workflow = _workflow_service.get_workflow(workflow_id)
    if workflow.owner_scope.scoped_id != owner.scoped_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft = _workflow_service.get_draft(workflow_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return {
        "workflow_id": str(draft.workflow_id),
        "draft_version": draft.draft_version,
        "graph": draft.graph,
        "config": draft.config,
        "layout": draft.layout,
        "graph_hash": draft.graph_hash,
        "execution_hash": draft.execution_hash,
    }


@router.put("/{workflow_id}/draft")
async def save_draft(workflow_id: uuid.UUID, body: SaveDraftRequest, authorization: str | None = Header(None)):
    """Save a draft using graph-hash compare-and-swap."""
    try:
        if _workflow_service.get_workflow(workflow_id).owner_scope.scoped_id != _resolve_owner(authorization).scoped_id:
            raise HTTPException(status_code=404, detail="Workflow not found")
        draft = _workflow_service.save_draft(
            workflow_id=workflow_id,
            graph=body.graph,
            config=body.config,
            layout=body.layout,
            base_graph_hash=body.base_graph_hash,
            pinned_dependency_revisions=body.pinned_dependency_revisions,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict())
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict())
    return draft


@router.post("/{workflow_id}/compile")
async def compile_workflow(workflow_id: uuid.UUID, authorization: str | None = Header(None)):
    """Compile a workflow revision into an execution plan.

    Loads the latest active revision or current draft, resolves the
    registry snapshot, and runs the compiler. Returns the plan or
    structured diagnostics on failure.
    """
    owner = _resolve_owner(authorization)
    if _workflow_service.get_workflow(workflow_id).owner_scope.scoped_id != owner.scoped_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    # 1. Get the active revision or current draft
    revision = _workflow_service.get_active_revision(workflow_id)
    if revision:
        graph = _workflow_service.get_revision_graph(revision.revision_id)
    else:
        draft = _workflow_service.get_draft(workflow_id)
        if not draft:
            raise HTTPException(status_code=404, detail="No draft or revision found")
        graph = draft.graph

    # 2. Get the latest registry snapshot
    try:
        registry = _registry_for_revision(revision.revision_id if revision else None) if revision else _snapshot_for_graph(graph, owner, persist=False)
    except (ForbiddenError, ValidationError_, NotFoundError) as exc:
        return _failed_diagnostic(exc.message, "agent")
    if registry is None:
        return _failed_diagnostic(
            "激活 Revision 缺少其固定的 RegistrySnapshot" if revision else "没有可用的 RegistrySnapshot",
        )

    try:
        _assert_graph_reference_authorization(graph, owner)
    except (ForbiddenError, NotFoundError, ValidationError_) as exc:
        return _failed_diagnostic(exc.message, "input_ref")

    # 3. Run the compiler
    try:
        plan = _compiler.compile(
            workflow_revision_id=revision.revision_id if revision else uuid.uuid4(),
            graph=graph,
            registry_snapshot=registry,
        )
        return {
            "status": "compiled",
            "plan_id": str(plan.plan_id),
            "plan_hash": plan.plan_hash,
            "diagnostics": [],
        }
    except CompilationError as e:
        return {
            "status": "failed",
            "diagnostics": e.details.get("diagnostics", []),
        }


@router.post("/{workflow_id}/revisions", status_code=201)
async def publish_revision(workflow_id: uuid.UUID, authorization: str | None = Header(None)) -> dict[str, Any]:
    """Freeze the current Draft into an immutable revision for its owner."""
    owner = _resolve_owner(authorization)
    try:
        workflow = _workflow_service.get_workflow(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    if workflow.owner_scope.scoped_id != owner.scoped_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    # Publication freezes the registry that is active now. Reusing the most
    # recent snapshot could silently compile a new revision against a stale
    # catalog that predates an official/public node baseline.
    draft = _workflow_service.get_draft(workflow_id)
    try:
        _assert_graph_reference_authorization(draft.graph, owner)
    except (ForbiddenError, NotFoundError, ValidationError_) as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    try:
        snapshot = _snapshot_for_graph(draft.graph, owner, persist=True)
    except (ForbiddenError, ValidationError_, NotFoundError) as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    try:
        revision, plan = _workflow_service.publish_compiled_revision(workflow_id, snapshot, _compiler)
    except CompilationError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc
    return {
        "workflow_id": str(revision.workflow_id),
        "revision_id": str(revision.revision_id),
        "registry_snapshot_id": str(revision.registry_snapshot_id),
        "status": revision.revision_status.value,
        "compiled_plan_id": str(plan.plan_id),
    }


@router.get("/{workflow_id}/revisions/{revision_id}/diff")
async def diff_revision_against_draft(
    workflow_id: uuid.UUID,
    revision_id: uuid.UUID,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Return a structural, owner-scoped diff before a rollback confirmation."""
    owner = _resolve_owner(authorization)
    try:
        workflow = _workflow_service.get_workflow(workflow_id)
        if workflow.owner_scope.scoped_id != owner.scoped_id:
            raise NotFoundError("Workflow", str(workflow_id))
        diff = _workflow_service.diff_draft_vs_revision(workflow_id, revision_id)
        draft = _workflow_service.get_draft(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return {
        "workflow_id": str(workflow_id),
        "revision_id": str(revision_id),
        "draft_graph_hash": draft.graph_hash,
        "diff": {
            "nodes_added": diff.nodes_added,
            "nodes_removed": diff.nodes_removed,
            "nodes_modified": diff.nodes_modified,
            "edges_added": diff.edges_added,
            "edges_removed": diff.edges_removed,
            "config_changed": diff.config_changed,
            "layout_changed": diff.layout_changed,
            "pinned_deps_changed": diff.pinned_deps_changed,
        },
    }


@router.post("/{workflow_id}/revisions/{revision_id}/rollback")
async def rollback_revision_to_draft(
    workflow_id: uuid.UUID,
    revision_id: uuid.UUID,
    body: RollbackDraftRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Create a new mutable Draft from an old revision after owner confirmation."""
    owner = _resolve_owner(authorization)
    if body.confirm_revision_id != revision_id:
        raise HTTPException(status_code=422, detail="confirm_revision_id must match rollback target")
    try:
        workflow = _workflow_service.get_workflow(workflow_id)
        if workflow.owner_scope.scoped_id != owner.scoped_id:
            raise NotFoundError("Workflow", str(workflow_id))
        draft = _workflow_service.rollback_to_revision(
            workflow_id,
            revision_id,
            base_graph_hash=body.base_graph_hash,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    return {
        "workflow_id": str(draft.workflow_id),
        "draft_version": draft.draft_version,
        "base_revision_id": str(draft.base_revision_id) if draft.base_revision_id else None,
        "graph_hash": draft.graph_hash,
        "execution_hash": draft.execution_hash,
    }
@router.post("/{workflow_id}/compile/dry-run")
async def dry_run_compile(workflow_id: uuid.UUID, authorization: str | None = Header(None)):
    """Dry-run compilation — always returns diagnostics, never raises."""
    owner = _resolve_owner(authorization)
    if _workflow_service.get_workflow(workflow_id).owner_scope.scoped_id != owner.scoped_id:
        raise HTTPException(status_code=404, detail="Workflow not found")
    revision = _workflow_service.get_active_revision(workflow_id)
    try:
        draft = _workflow_service.get_draft(workflow_id)
    except NotFoundError:
        return {"passes": False, "diagnostics": [_failed_diagnostic("未找到工作流 Draft", "workflow")["diagnostics"][0]]}
    graph = _workflow_service.get_revision_graph(revision.revision_id) if revision else draft.graph

    try:
        registry = _registry_for_revision(revision.revision_id if revision else None) if revision else _snapshot_for_graph(graph, owner, persist=False)
    except (ForbiddenError, ValidationError_, NotFoundError) as exc:
        return {"passes": False, "diagnostics": [_failed_diagnostic(exc.message, "agent")["diagnostics"][0]]}
    if registry is None:
        return {"passes": False, "diagnostics": [_failed_diagnostic("没有可用的 RegistrySnapshot")["diagnostics"][0]]}

    passes, diagnostics = _compiler.dry_run(graph=graph, registry_snapshot=registry)
    return {
        "passes": passes,
        "diagnostics": diagnostics,
    }


@router.get("/by-owner")
async def list_workflows_by_owner(authorization: str | None = Header(None)):
    """List workflows belonging to a given owner."""
    scope = _resolve_owner(authorization)
    rows = _workflow_service.list_workflows(owner_scope=scope)
    return {
        "workflows": [
            {
                "workflow_id": str(item.workflow_id),
                "owner_scope": item.owner_scope.scoped_id,
            }
            for item in rows
        ]
    }
