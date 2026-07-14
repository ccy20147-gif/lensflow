"""Contract tests for the workflow node catalog capability report."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from src.app import app
from src.api.routes import registry as registry_routes
from src.schemas.models import NodeDefinitionRevision, PortTypeRef


class _RegistryDouble:
    def __init__(self, definitions: list[NodeDefinitionRevision]) -> None:
        self._definitions = definitions

    def list_node_definitions(self, *, status: str | None = None, type_id: str | None = None) -> list[NodeDefinitionRevision]:
        assert status == "active"
        return self._definitions


def _definition(*, executor_ref: str) -> NodeDefinitionRevision:
    return NodeDefinitionRevision(
        node_type_id="toonflow.test.node",
        revision_id=uuid4(),
        semantic_version="1.0.0",
        input_ports=[PortTypeRef(port_id="input", type_id="text", schema_id="toonflow.text", schema_version=1, cardinality="required")],
        output_ports=[PortTypeRef(port_id="output", type_id="text", schema_id="toonflow.text", schema_version=1, cardinality="required")],
        executor_ref=executor_ref,
    )


def test_catalog_reports_unconfigured_atlascloud_without_demo_claims(monkeypatch) -> None:
    monkeypatch.setattr(registry_routes, "_registry", _RegistryDouble([_definition(executor_ref="atlas://text")]))
    monkeypatch.setattr(registry_routes.settings, "atlascloud_api_key", "")

    result = asyncio.run(registry_routes.catalog())

    assert result["provider"] == {"provider_id": "atlascloud", "configured": False}
    assert "demo_note" not in result
    assert result["node_types"][0]["provider_required"] is True
    assert result["node_types"][0]["execution_available"] is False


def test_catalog_reports_configured_atlascloud_for_provider_nodes(monkeypatch) -> None:
    monkeypatch.setattr(registry_routes, "_registry", _RegistryDouble([_definition(executor_ref="atlas://text")]))
    monkeypatch.setattr(registry_routes.settings, "atlascloud_api_key", "test-key")

    result = asyncio.run(registry_routes.catalog())

    assert result["provider"]["configured"] is True
    assert result["node_types"][0]["execution_available"] is True


def test_catalog_http_returns_immutable_definition_revision_id(monkeypatch) -> None:
    definition = _definition(executor_ref="")
    monkeypatch.setattr(registry_routes, "_registry", _RegistryDouble([definition]))
    with TestClient(app) as client:
        response = client.get("/api/v1/registry/catalog")

    assert response.status_code == 200
    entry = next(item for item in response.json()["node_types"] if item["type_id"] == definition.node_type_id)
    assert entry["revision_id"] == str(definition.revision_id)
