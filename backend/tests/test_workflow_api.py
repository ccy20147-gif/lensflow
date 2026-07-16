"""Black-box API contracts for draft save and workflow compilation."""
from __future__ import annotations

from uuid import uuid4

import pytest
import httpx

from src.app import app
from src.api.routes import workflow as workflow_routes
from src.domain.workflow.registry_service import RegistryService
from src.domain.workflow.workflow_service import WorkflowService
from src.schemas.models import NodeDefinitionRevision, PortTypeRef
from src.schemas.models import OwnerScope


TEST_OWNER = OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[WorkflowService, RegistryService]:
    workflows = WorkflowService()
    registry = RegistryService()
    monkeypatch.setattr(workflow_routes, "_workflow_service", workflows)
    monkeypatch.setattr(workflow_routes, "_registry_service", registry)
    monkeypatch.setattr(workflow_routes, "_resolve_owner", lambda _authorization: TEST_OWNER)
    return workflows, registry


async def api_request(method: str, url: str, **kwargs: object) -> httpx.Response:
    """Exercise FastAPI routing without TestClient's sync portal."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, url, **kwargs)


def _active_snapshot(registry: RegistryService):
    definition = NodeDefinitionRevision(
        node_type_id="test.input",
        revision_id=uuid4(),
        semantic_version="1.0.0",
        executor_ref="workflow.test.input",
        policy_metadata={"package_source": "approved:workflow-api-test"},
        output_ports=[PortTypeRef(port_id="out", type_id="text", schema_id="text", schema_version=1, cardinality="optional")],
    )
    registry.register_definition(definition)
    registry.activate_definition(definition.node_type_id, definition.revision_id)
    return registry.generate_snapshot()


@pytest.mark.asyncio
async def test_compile_uses_frozen_active_revision_graph(client: tuple[WorkflowService, RegistryService]) -> None:
    workflows, registry = client
    workflow = workflows.create_workflow(owner_scope=TEST_OWNER)
    draft = workflows.get_draft(workflow.workflow_id)
    graph = {"nodes": [{"id": "source", "type": "test.input"}], "edges": []}
    save = await api_request(
        "PUT",
        f"/api/v1/workflows/{workflow.workflow_id}/draft",
        json={"graph": graph, "config": {}, "layout": {}, "base_graph_hash": draft.graph_hash},
    )
    assert save.status_code == 200
    revision = workflows.create_revision_from_draft(workflow.workflow_id, _active_snapshot(registry).snapshot_id)
    # A later draft change must not alter the graph pinned by the active revision.
    current = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(workflow.workflow_id, {"nodes": [], "edges": []}, {}, {}, current.graph_hash)

    response = await api_request("POST", f"/api/v1/workflows/{workflow.workflow_id}/compile")
    assert response.status_code == 200
    assert response.json()["status"] == "compiled"
    assert revision.graph == graph


@pytest.mark.asyncio
async def test_compile_reports_missing_pinned_snapshot(client: tuple[WorkflowService, RegistryService]) -> None:
    workflows, _registry = client
    workflow = workflows.create_workflow(owner_scope=TEST_OWNER)
    draft = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(
        workflow.workflow_id,
        {"nodes": [{"id": "missing", "type": "missing.type"}], "edges": []},
        {}, {}, draft.graph_hash,
    )
    workflows.create_revision_from_draft(workflow.workflow_id, uuid4())

    response = await api_request("POST", f"/api/v1/workflows/{workflow.workflow_id}/compile")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "RegistrySnapshot" in response.json()["diagnostics"][0]["message"]


@pytest.mark.asyncio
async def test_dry_run_requires_active_revision(client: tuple[WorkflowService, RegistryService]) -> None:
    """TF-WF-003 FR-1: the dry-run preview refuses a mutable draft.

    Before any owner-confirmed activation, the workflow has no active
    revision; the preview must surface a structured refusal so the
    canvas cannot iterate against an unauthorised plan hash.
    """
    workflows, registry = client
    workflow = workflows.create_workflow(owner_scope=TEST_OWNER)
    _active_snapshot(registry)

    response = await api_request("POST", f"/api/v1/workflows/{workflow.workflow_id}/compile/dry-run")
    assert response.status_code == 200
    body = response.json()
    assert body["passes"] is False
    diagnostic = body["diagnostics"][0]
    assert diagnostic["location"] == "compile_input"
    assert "ACTIVE Revision" in diagnostic["message"]


@pytest.mark.asyncio
async def test_save_draft_returns_cas_conflict(client: tuple[WorkflowService, RegistryService]) -> None:
    workflows, _registry = client
    workflow = workflows.create_workflow(owner_scope=TEST_OWNER)

    response = await api_request(
        "PUT",
        f"/api/v1/workflows/{workflow.workflow_id}/draft",
        json={"graph": {}, "config": {}, "layout": {}, "base_graph_hash": "not-current"},
    )
    assert response.status_code == 409
