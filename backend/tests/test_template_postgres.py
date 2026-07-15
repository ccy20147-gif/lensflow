"""PostgreSQL acceptance tests for durable TF-WF-009 template packages."""
from __future__ import annotations

from copy import deepcopy
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from src.core.exceptions import ConflictError, PolicyBlockedError
from src.domain.template.template_service import PackageDependency, ReplacementSlot, WorkflowPackageManifest
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.session import get_session_factory
from src.infra.db.models import WorkflowRevisionModel, WorkflowTemplateModel
from src.infra.db.template_repository import (
    BENCHMARK_TEMPLATE_CONTENT_HASHES,
    BENCHMARK_TEMPLATE_GRAPHS,
    SqlTemplateService,
)
from src.schemas.enums import DependencyKind
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


@pytest.fixture
def factory():
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    return factory


@pytest.fixture
def source_revision(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    saved = workflows.save_draft(
        workflow.workflow_id,
        graph={"nodes": [{"id": "brief", "type": "brief"}], "edges": []},
        config={"source": "pinned"}, layout={"brief": {"x": 10, "y": 20}},
        base_graph_hash=draft.graph_hash,
    )
    return workflows.create_revision_from_draft(workflow.workflow_id, uuid4()), saved


def test_template_instance_is_durable_clone_with_pinned_source_graph(factory, source_revision):
    source, _ = source_revision
    owner = OwnerScope(kind="user", id=uuid4())
    templates = SqlTemplateService(factory)
    template_id = templates.create_template("durable", str(source.revision_id), parameter_schema={"type": "object"})

    instance = templates.instantiate_template(template_id, owner, parameters={"brief": "story"})
    # A new service models a restart: all package and lineage state persists.
    after_restart = SqlTemplateService(factory)
    loaded = after_restart.get_instance(instance.instance_id)
    draft = SqlWorkflowService(factory).get_draft(UUID(loaded.workflow_id))
    assert draft.base_revision_id == source.revision_id
    assert draft.graph == {"nodes": [{"id": "brief", "type": "brief"}], "edges": []}
    assert draft.layout == {"brief": {"x": 10, "y": 20}}
    assert draft.config["source"] == "pinned"
    assert draft.config["template_parameters"] == {"brief": "story"}
    assert loaded.attribution_manifest["template_revision_id"] == str(source.revision_id)


def test_template_blocks_secret_missing_dependency_and_accepts_typed_replacement(factory, source_revision):
    source, _ = source_revision
    owner = OwnerScope(kind="user", id=uuid4())
    templates = SqlTemplateService(factory)
    with pytest.raises(PolicyBlockedError):
        templates.create_template("secret", str(source.revision_id), default_mapping={"api_key": "must-not-package"})

    missing = WorkflowPackageManifest(name="missing", dependencies=[
        PackageDependency("gone", DependencyKind.WORKFLOW, str(uuid4())),
    ])
    missing_id = templates.create_template("missing", str(source.revision_id), manifest=missing)
    assert templates.resolve_dependencies(missing_id)["missing"] == ["gone"]
    with pytest.raises(ConflictError):
        templates.instantiate_template(missing_id, owner)

    replacement = WorkflowPackageManifest(
        name="replacement",
        dependencies=[PackageDependency("model", DependencyKind.PROVIDER, "atlas/model", replacement_slot="model_slot")],
        replacement_slots=[ReplacementSlot("model_slot", "Model", expected_kind=DependencyKind.PROVIDER)],
    )
    replacement_id = templates.create_template("replacement", str(source.revision_id), manifest=replacement)
    instance = templates.instantiate_template(replacement_id, owner, replacements={"model_slot": "atlas/model-v2"})
    assert instance.dependency_resolution == {"model": "atlas/model-v2"}


def test_template_manifest_rejects_duplicate_dependency_cycle_marker(factory, source_revision):
    source, _ = source_revision
    templates = SqlTemplateService(factory)
    manifest = WorkflowPackageManifest(name="cycle", dependencies=[
        PackageDependency("same", DependencyKind.WORKFLOW, str(source.revision_id)),
        PackageDependency("same", DependencyKind.WORKFLOW, str(source.revision_id)),
    ])
    with pytest.raises(ConflictError):
        templates.create_template("cycle", str(source.revision_id), manifest=manifest)


def test_template_dependency_invalid_uuid_is_structured_missing(factory, source_revision):
    source, _ = source_revision
    templates = SqlTemplateService(factory)
    manifest = WorkflowPackageManifest(name="bad-agent", dependencies=[
        PackageDependency("agent", DependencyKind.AGENT, "not-a-uuid"),
    ])
    template_id = templates.create_template("bad-agent", str(source.revision_id), manifest=manifest)
    result = templates.resolve_dependencies(template_id, owner_scope=OwnerScope(kind="user", id=uuid4()))
    assert result["resolved"] is False
    assert result["missing"] == ["agent"]
    assert result["diagnostics"] == [{
        "code": "INVALID_UUID", "dep_id": "agent", "kind": "agent", "revision_id": "not-a-uuid",
        "message": "Dependency revision_id must be a UUID", "path": [template_id, "agent"],
    }]


def test_template_transitive_closure_and_typed_replacement_are_revalidated(factory):
    """Nested manifests cannot hide invalid references or bypass slot typing."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    source = workflows.create_revision_from_draft(workflow.workflow_id, uuid4())
    templates = SqlTemplateService(factory)

    child_id = templates.create_template(
        "child", str(source.revision_id),
        manifest=WorkflowPackageManifest(name="child", dependencies=[
            PackageDependency("broken_agent", DependencyKind.AGENT, "not-a-uuid"),
        ]), owner_scope=owner,
    )
    parent_id = templates.create_template(
        "parent", str(source.revision_id),
        manifest=WorkflowPackageManifest(name="parent", dependencies=[
            PackageDependency("child", DependencyKind.TEMPLATE, child_id),
            PackageDependency("world", DependencyKind.RESOURCE, "ignored", replacement_slot="world_slot"),
        ], replacement_slots=[ReplacementSlot("world_slot", "World", expected_kind=DependencyKind.RESOURCE)]),
        owner_scope=owner,
    )
    resolved = templates.resolve_dependencies(parent_id, {"world_slot": "not-a-uuid"}, owner)
    assert resolved["resolved"] is False
    assert "child" in resolved["missing"]
    codes = {item["code"] for item in resolved["diagnostics"]}
    assert {"INVALID_UUID"} <= codes
    # The nested error keeps the full path, rather than collapsing to a
    # generic unavailable template that cannot be corrected by the author.
    nested = next(item for item in resolved["diagnostics"] if item["dep_id"] == "broken_agent")
    assert nested["path"] == [parent_id, "child", child_id, "broken_agent"]


def test_import_manifest_revalidates_nested_typed_replacement_slots(factory):
    """A nested package cannot hide a required replacement behind its template ID."""
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService(factory)
    source = workflows.create_workflow(owner_scope=owner)
    source_revision = workflows.create_revision_from_draft(source.workflow_id, uuid4())
    templates = SqlTemplateService(factory)
    child_id = templates.create_template(
        "import-child", str(source_revision.revision_id), owner_scope=owner,
        manifest=WorkflowPackageManifest(
            name="import-child",
            dependencies=[PackageDependency("provider", DependencyKind.PROVIDER, "atlascloud/pending", replacement_slot="provider_slot")],
            replacement_slots=[ReplacementSlot("provider_slot", "Provider", expected_kind=DependencyKind.PROVIDER)],
        ),
    )
    wrapper = WorkflowPackageManifest(
        name="import-wrapper",
        dependencies=[PackageDependency("child", DependencyKind.TEMPLATE, child_id)],
    )
    unresolved = templates.resolve_import_manifest(wrapper, replacements={}, owner_scope=owner)
    assert unresolved["resolved"] is False
    assert unresolved["diagnostics"][0]["code"] == "REPLACEMENT_REQUIRED"
    assert unresolved["diagnostics"][0]["path"] == ["import", "child", child_id, "provider"]
    resolved = templates.resolve_import_manifest(wrapper, replacements={"provider_slot": "atlascloud/llm/demo"}, owner_scope=owner)
    assert resolved["resolved"] is True


def test_benchmark_template_seed_uses_only_public_business_nodes(factory):
    owner = OwnerScope(kind="user", id=uuid4())
    templates = SqlTemplateService(factory)
    ids = templates.seed_benchmark_templates(owner)
    assert len(ids) == 2
    assert templates.seed_benchmark_templates(owner) == ids
    allowed = {"brief", "constraint", "structured_generate", "model_router", "variants", "select_rank", "review", "workbench_task", "package_export"}
    assert all({node["type"] for node in graph["nodes"]} <= allowed for graph in BENCHMARK_TEMPLATE_GRAPHS.values())


def test_benchmark_seed_replaces_legacy_graph_and_instantiates_typed_edges(factory):
    """A historical same-name package cannot shadow the current benchmark."""
    maintainer = OwnerScope(kind="user", id=uuid4())
    consumer = OwnerScope(kind="user", id=uuid4())
    templates = SqlTemplateService(factory)
    workflows = SqlWorkflowService(factory)
    name = "广告创意候选与人工精修"
    legacy_graph = deepcopy(BENCHMARK_TEMPLATE_GRAPHS[name])
    legacy_graph["edges"] = [
        {"source": edge["source"], "target": edge["target"]}
        for edge in legacy_graph["edges"]
    ]
    legacy_workflow = workflows.create_workflow(owner_scope=maintainer)
    legacy_draft = workflows.get_draft(legacy_workflow.workflow_id)
    workflows.save_draft(legacy_workflow.workflow_id, legacy_graph, {}, {}, legacy_draft.graph_hash)
    legacy_revision = workflows.create_revision_from_draft(legacy_workflow.workflow_id, uuid4())
    legacy_template_id = templates.create_template(
        name,
        str(legacy_revision.revision_id),
        manifest=WorkflowPackageManifest(name=name, version="benchmark-legacy"),
        visibility="public",
        owner_scope=maintainer,
    )

    seeded_ids = templates.seed_benchmark_templates(maintainer)
    assert templates.seed_benchmark_templates(maintainer) == seeded_ids
    current_id = seeded_ids[0]
    assert current_id != legacy_template_id

    with factory() as session:
        legacy = session.get(WorkflowTemplateModel, UUID(legacy_template_id))
        current = session.get(WorkflowTemplateModel, UUID(current_id))
        assert legacy is not None and legacy.revision_status.value == "retired"
        assert current is not None and current.revision_status.value == "active"
        assert current.manifest["version"] == f"benchmark-{BENCHMARK_TEMPLATE_CONTENT_HASHES[name]}"
        for template_id in seeded_ids:
            seeded = session.get(WorkflowTemplateModel, UUID(template_id))
            assert seeded is not None and seeded.revision_status.value == "active"
            source = session.get(WorkflowRevisionModel, seeded.workflow_revision_id)
            assert source is not None
            assert all(edge.get("sourceHandle") and edge.get("targetHandle") for edge in source.graph["edges"])

    for template_id in seeded_ids:
        instance = templates.instantiate_template(template_id, consumer)
        draft = workflows.get_draft(UUID(instance.workflow_id))
        assert all(edge.get("sourceHandle") and edge.get("targetHandle") for edge in draft.graph["edges"])
