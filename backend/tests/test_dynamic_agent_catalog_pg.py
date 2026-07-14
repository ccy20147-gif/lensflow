"""AGT-002/004: owner-scoped published Agent nodes enter a frozen workflow plan."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.app import app
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.identity_repository import get_session_store
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.session import get_session_factory


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


def _headers(owner_id):
    return {"Authorization": f"Bearer {get_session_store().issue(owner_id)['token']}"}


def test_published_agent_catalog_is_owner_scoped_and_compiles_as_pinned_nodes() -> None:
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    owner_id, other_id = uuid4(), uuid4()
    owner_scope = f"user:{owner_id}"
    agents = SqlAgentRepository(factory)
    definition = agents.create_definition(name="Three-stage writer", description="typed", agent_kind="configurable", owner_scope=owner_scope)
    revision = agents.create_revision(definition.agent_id, {
        "input_schema_ref": "toonflow.story.v1", "output_schema_ref": "toonflow.story.v1",
        "sop_steps": [{"step_id": "write", "instruction": "Return a typed story artifact"}],
        "execution_policy": {"provider_ref": "atlascloud/test"},
    })
    revision = agents.promote_revision(revision.revision_id)
    # The base registry must exist independently; publication adds the private
    # Agent definition only to this workflow's immutable snapshot.
    SqlRegistryService(factory).create_snapshot()
    node_type = f"agent.invoke.{revision.revision_id}"
    with TestClient(app) as client:
        published_agents = client.get("/api/v1/agents/published", headers=_headers(owner_id))
        assert published_agents.status_code == 200
        assert published_agents.json()["agents"][0]["revision_id"] == str(revision.revision_id)
        catalog = client.get("/api/v1/registry/catalog", headers=_headers(owner_id))
        assert catalog.status_code == 200
        entry = next(node for node in catalog.json()["node_types"] if node["type_id"] == node_type)
        assert entry["config"]["agent_revision_id"] == str(revision.revision_id)
        assert entry["input_ports"][0]["schema_id"] == "toonflow.story"
        assert all(node["type_id"] != node_type for node in client.get("/api/v1/registry/catalog", headers=_headers(other_id)).json()["node_types"])

        workflow = client.post("/api/v1/workflows/", headers=_headers(owner_id), json={})
        assert workflow.status_code == 201
        workflow_id = workflow.json()["workflow_id"]
        draft = client.get(f"/api/v1/workflows/{workflow_id}/draft", headers=_headers(owner_id)).json()
        graph = {
            "nodes": [
                {"id": name, "type": node_type, "data": {"node_type_id": node_type, "config": {"agent_revision_id": str(revision.revision_id)}}}
                for name in ("world", "outline", "expand")
            ],
            "edges": [
                {"id": "world-outline", "source": "world", "target": "outline", "sourceHandle": "output", "targetHandle": "input"},
                {"id": "outline-expand", "source": "outline", "target": "expand", "sourceHandle": "output", "targetHandle": "input"},
            ],
        }
        saved = client.put(f"/api/v1/workflows/{workflow_id}/draft", headers=_headers(owner_id), json={"graph": graph, "config": {}, "layout": {}, "base_graph_hash": draft["graph_hash"], "pinned_dependency_revisions": [str(revision.revision_id)]})
        assert saved.status_code == 200
        compiled = client.post(f"/api/v1/workflows/{workflow_id}/compile", headers=_headers(owner_id))
        assert compiled.status_code == 200 and compiled.json()["status"] == "compiled"
        published = client.post(f"/api/v1/workflows/{workflow_id}/revisions", headers=_headers(owner_id))
        assert published.status_code == 201
        snapshot = SqlRegistryService(factory).get_snapshot(UUID(published.json()["registry_snapshot_id"]))
        assert node_type in snapshot.node_definitions
