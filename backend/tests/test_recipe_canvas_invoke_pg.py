"""TF-MR-001: a published Recipe is one frozen outer canvas node."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from src.app import app
from src.domain.runtime.runtime_service import RuntimeService
from src.domain.runtime.worker import RuntimeWorker
from src.infra.db.identity_repository import get_session_store
from src.infra.db.models import NodeRunAttemptModel, NodeRunModel
from src.infra.db.recipe_repository import SqlRecipeRepository
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.session import get_session_factory
from src.schemas.enums import NodeRunStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


def _headers(owner_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {get_session_store().issue(owner_id)['token']}"}


def test_published_recipe_is_owner_scoped_canvas_node_and_expands_only_at_runtime() -> None:
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    owner_id, other_id = uuid4(), uuid4()
    owner = OwnerScope(kind="user", id=owner_id)
    recipes = SqlRecipeRepository(factory)
    recipe = recipes.create_definition(name="Fixed input recipe", description="internal only", owner_scope=owner.scoped_id, recipe_type="image")
    frozen = recipes.create_revision(recipe.recipe_id, {
        "recipe_type": "image",
        "public_input_schema_refs": ["toonflow.prompt.v1"],
        "public_output_schema_refs": ["toonflow.media_output.v1"],
        "operator_graph": {"source": {"type": "input", "outputs": ["prompt"]}},
    })
    frozen = recipes.promote_revision(frozen.revision_id)
    node_type = f"media.recipe.{frozen.revision_id}"
    registry = SqlRegistryService(factory)
    registry.create_snapshot()

    with TestClient(app) as client:
        catalog = client.get("/api/v1/registry/catalog", headers=_headers(owner_id))
        assert catalog.status_code == 200
        entry = next(node for node in catalog.json()["node_types"] if node["type_id"] == node_type)
        assert entry["config"] == {"media_recipe_revision_id": str(frozen.revision_id)}
        assert entry["category"] == "Media Recipes"
        assert all(node["type_id"] != node_type for node in client.get("/api/v1/registry/catalog", headers=_headers(other_id)).json()["node_types"])

        workflow = client.post("/api/v1/workflows/", headers=_headers(owner_id), json={})
        workflow_id = workflow.json()["workflow_id"]
        draft = client.get(f"/api/v1/workflows/{workflow_id}/draft", headers=_headers(owner_id)).json()
        graph = {"nodes": [{"id": "recipe", "type": node_type, "data": {"node_type_id": node_type, "config": entry["config"]}}], "edges": []}
        saved = client.put(f"/api/v1/workflows/{workflow_id}/draft", headers=_headers(owner_id), json={"graph": graph, "config": {}, "layout": {}, "base_graph_hash": draft["graph_hash"], "pinned_dependency_revisions": [str(frozen.revision_id)]})
        assert saved.status_code == 200
        assert client.post(f"/api/v1/workflows/{workflow_id}/compile", headers=_headers(owner_id)).json()["status"] == "compiled"
        published = client.post(f"/api/v1/workflows/{workflow_id}/revisions", headers=_headers(owner_id))
        assert published.status_code == 201
        snapshot = registry.get_snapshot(UUID(published.json()["registry_snapshot_id"]))
        assert node_type in snapshot.node_definitions

    workflow_revision_id = UUID(published.json()["revision_id"])
    plan = CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=workflow_revision_id, registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()), resolved_graph=graph, plan_hash="recipe-canvas")
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=owner)
    runtime.start_run(run.run_id)
    worker = RuntimeWorker(factory)
    parent = worker.claim_next_attempt("recipe-worker", run_id=run.run_id)
    assert parent is not None
    materialized = worker.execute_attempt(parent.attempt.attempt_id)
    assert materialized["kind"] == "media_recipe_invoke"
    with factory() as session:
        nodes = list(session.scalars(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id)))
        assert {node.node_instance_id for node in nodes} == {"recipe", "recipe:recipe:source"}
    child = worker.claim_next_attempt("recipe-worker", run_id=run.run_id)
    assert child is not None
    complete = worker.execute_attempt(child.attempt.attempt_id)
    assert complete["kind"] == "media_recipe_operator"
    with factory() as session:
        outer = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id, NodeRunModel.node_instance_id == "recipe"))
        assert outer is not None and outer.status == NodeRunStatus.COMPLETED
        parent_attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == outer.node_run_id))
        assert parent_attempt is not None and str(parent_attempt.fixed_input["media_recipe_revision_id"]) == str(frozen.revision_id)
