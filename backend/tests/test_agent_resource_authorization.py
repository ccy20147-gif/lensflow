"""HTTP ownership boundaries for 3.2 authored resources."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.app import app
from src.infra.db.identity_repository import get_session_store
from src.infra.db.agent_repository import SqlAgentRepository
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.session import get_session_factory
from src.infra.db.skill_repository import SqlSkillRepository
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


@pytest.fixture
def client() -> TestClient:
    factory = get_session_factory()
    with factory() as session:
        session.execute(text("SELECT 1"))
    with TestClient(app) as value:
        yield value


def _headers(user_id):
    return {"Authorization": f"Bearer {get_session_store().issue(user_id)['token']}"}


def test_agent_and_recipe_mutations_are_bearer_owner_scoped(client: TestClient):
    owner, other = uuid4(), uuid4()
    agent = client.post("/api/v1/agents", headers=_headers(owner), json={"name": "owned"})
    assert agent.status_code == 200
    agent_id = agent.json()["agent_id"]
    assert client.get(f"/api/v1/agents/{agent_id}", headers=_headers(other)).status_code == 403
    assert client.patch(f"/api/v1/agents/{agent_id}", headers=_headers(other), json={"name": "stolen"}).status_code == 403

    recipe = client.post("/api/v1/recipes", headers=_headers(owner), json={"name": "owned", "recipe_type": "image"})
    assert recipe.status_code == 200
    recipe_id = recipe.json()["recipe_id"]
    assert client.get(f"/api/v1/recipes/{recipe_id}", headers=_headers(other)).status_code == 403
    assert client.delete(f"/api/v1/recipes/{recipe_id}", headers=_headers(other)).status_code == 403


def test_recipe_revision_diff_is_owner_scoped_and_returns_frozen_contract(client: TestClient):
    owner, other = uuid4(), uuid4()
    headers = _headers(owner)
    recipe = client.post("/api/v1/recipes", headers=headers, json={"name": "revision-diff", "recipe_type": "image"})
    assert recipe.status_code == 200
    recipe_id = recipe.json()["recipe_id"]
    first_body = {
        "recipe_type": "image", "operator_graph": {"source": {"type": "input", "outputs": ["prompt"]}},
        "public_input_schema_refs": ["toonflow.prompt.v1"], "public_output_schema_refs": ["toonflow.media_output.v1"],
    }
    first = client.post(f"/api/v1/recipes/{recipe_id}/revisions", headers=headers, json={"body": first_body})
    assert first.status_code == 200
    assert first.json()["revision_status"] == "draft"
    second_body = {**first_body, "parameter_schema": {"type": "object", "properties": {"seed": {"type": "integer"}}}}
    second = client.post(f"/api/v1/recipes/{recipe_id}/revisions", headers=headers, json={"body": second_body, "base_hash": first.json()["content_hash"]})
    assert second.status_code == 200
    diff_path = f"/api/v1/recipes/{recipe_id}/revisions/{first.json()['revision_id']}/diff/{second.json()['revision_id']}"
    assert client.get(diff_path, headers=_headers(other)).status_code == 403
    diff = client.get(diff_path, headers=headers)
    assert diff.status_code == 200
    assert diff.json()["changed_fields"] == ["parameter_schema"]


def test_skill_list_and_mutation_ignore_client_owner_scope(client: TestClient):
    owner, other = uuid4(), uuid4()
    created = client.post("/api/v1/skills", headers=_headers(owner), json={"name": "owned", "body": {"instructions": ["x"]}})
    assert created.status_code == 200
    skill_id = created.json()["skill_id"]
    assert client.get(f"/api/v1/skills/{skill_id}", headers=_headers(other)).status_code == 403
    assert client.patch(f"/api/v1/skills/{skill_id}", headers=_headers(other), json={"body": {"instructions": ["stolen"]}}).status_code == 403
    assert all(item["owner_scope"] == f"user:{other}" for item in client.get("/api/v1/skills", headers=_headers(other)).json())


def test_prepare_rejects_revoked_cross_owner_skill_ref_with_403(client: TestClient):
    source = OwnerScope(kind="user", id=uuid4())
    consumer = OwnerScope(kind="user", id=uuid4())
    factory = get_session_factory()
    skills = SqlSkillRepository(factory)
    skill = skills.create_skill(name="shared", description="", owner_scope=source.scoped_id, body={"instructions": ["x"]})
    frozen = skills.submit_revision(skill.skill_id, base_hash=skill.content_hash)
    resources = SqlResourceRepository(factory)
    grant = resources.grant(frozen.revision_id, source, consumer, capability_actions=["reference", "execute"])
    agents = SqlAgentRepository(factory)
    definition = agents.create_definition(name="consumer", description="", agent_kind="configurable", owner_scope=consumer.scoped_id)
    revision = agents.create_revision(definition.agent_id, {
        "skill_revision_refs": [{
            "resource_id": str(skill.skill_id), "resource_type": "skill",
            "revision_id": str(frozen.revision_id), "grant_snapshot_id": str(grant),
        }],
        "sop_steps": [{"step_id": "s", "instruction": "return object"}],
        "execution_policy": {"provider_ref": "atlascloud/test"},
    })
    agents.promote_revision(revision.revision_id)
    headers = _headers(consumer.id)
    request = {"agent_revision_id": str(revision.revision_id), "typed_inputs": {}}
    assert client.post("/api/v1/agents/invoke/prepare", headers=headers, json=request).status_code == 200
    resources.revoke_grant(frozen.revision_id, grant, source)
    assert client.post("/api/v1/agents/invoke/prepare", headers=headers, json=request).status_code == 403
