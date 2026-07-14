"""P0 HTTP contracts for the frozen Media Recipe execution boundary."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from src.app import app
from src.domain.provider.atlascloud import AtlasSubmission
from src.infra.db.models import (
    MediaRecipeDefinitionModel,
    MediaRecipeRevisionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    ProviderInvocationAttemptModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import AttemptStatus, NodeRunStatus, RevisionStatus, RunStatus


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def _identity() -> tuple[UUID, str]:
    email = f"recipe-exec-{uuid4()}@test.local"
    await _request("POST", "/api/v1/identity/register", json={"email": email, "display_name": "Recipe", "password": "Correct-Password-1"})
    login = await _request("POST", "/api/v1/identity/login", json={"email": email, "password": "Correct-Password-1"})
    return UUID(login.json()["account_id"]), login.json()["token"]


def _frozen_recipe(owner_scope: str, *, active: bool = True) -> tuple[UUID, dict]:
    body = {
        "recipe_type": "image",
        "public_output_schema_refs": ["media_output.v1"],
        "operator_graph": {"generate": {"type": "atlas_image", "model_id": "atlas/test", "inputs": []}},
        "compiled_plan": {"frozen": True},
    }
    recipe_id, revision_id = uuid4(), uuid4()
    with get_session_factory().begin() as session:
        session.add(MediaRecipeDefinitionModel(recipe_id=recipe_id, name="frozen", description="", owner_scope=owner_scope, recipe_type="image", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
        session.add(MediaRecipeRevisionModel(revision_id=revision_id, recipe_id=recipe_id, revision_number=1, body=body, content_hash="h", status="active" if active else "draft", created_at=datetime.now(timezone.utc)))
    return revision_id, body


def _parent(owner_scope: str, *, recipe_revision_id: UUID | None, inputs: dict | None = None) -> UUID:
    workflow_id, revision_id, run_id, node_id, attempt_id = (uuid4() for _ in range(5))
    now = datetime.now(timezone.utc)
    with get_session_factory().begin() as session:
        from src.infra.db.models import WorkflowModel
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=owner_scope, created_at=now))
        session.flush()
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1, graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), graph={}, config={}, layout={}, revision_status=RevisionStatus.ACTIVE, created_at=now))
        session.flush()
        session.add(WorkflowRunModel(run_id=run_id, workflow_revision_id=revision_id, compiled_plan_id=uuid4(), owner_scope=owner_scope, input_snapshot={}, status=RunStatus.RUNNING, created_at=now))
        session.flush()
        session.add(NodeRunModel(node_run_id=node_id, run_id=run_id, node_instance_id="recipe", node_type_id="media_recipe_invoke", status=NodeRunStatus.RUNNING))
        session.flush()
        fixed = {"recipe_inputs": inputs or {}}
        if recipe_revision_id is not None:
            fixed["recipe_revision_id"] = str(recipe_revision_id)
        session.add(NodeRunAttemptModel(attempt_id=attempt_id, node_run_id=node_id, attempt_number=1, execution_epoch=1, fixed_input=fixed, status=AttemptStatus.RUNNING))
    return attempt_id


def _effects(parent_attempt_id: UUID) -> tuple[int, int]:
    with get_session_factory()() as session:
        parent = session.get(NodeRunAttemptModel, parent_attempt_id)
        node = session.get(NodeRunModel, parent.node_run_id)
        children = session.scalar(select(func.count()).select_from(NodeRunModel).where(NodeRunModel.run_id == node.run_id, NodeRunModel.node_instance_id.like("recipe:recipe:%"))) or 0
        provider = session.scalar(select(func.count()).select_from(ProviderInvocationAttemptModel).join(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id)) or 0
        return int(children), int(provider)


@pytest.mark.asyncio
async def test_recipe_execute_rejects_foreign_unpinned_mismatch_and_inactive_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.api.routes.recipe as route

    class ConfiguredNeverCalled:
        configured = True
        def submit(self, **_kwargs: object) -> AtlasSubmission:
            raise AssertionError("Atlas must not be called for rejected executions")

    monkeypatch.setattr(route, "AtlasCloudAdapter", ConfiguredNeverCalled)
    owner_id, owner_token = await _identity()
    other_id, other_token = await _identity()
    owner_scope, other_scope = f"user:{owner_id}", f"user:{other_id}"
    active_revision, body = _frozen_recipe(owner_scope)
    foreign_parent = _parent(owner_scope, recipe_revision_id=active_revision)
    headers = {"Authorization": f"Bearer {other_token}"}
    payload = {"node_run_attempt_id": str(foreign_parent), "recipe_revision_id": str(active_revision), "idempotency_key": str(uuid4()), "inputs": {"prompt": "attacker"}, "body": body}
    assert (await _request("POST", "/api/v1/recipes/execute", headers=headers, json=payload)).status_code == 403
    assert _effects(foreign_parent) == (0, 0)

    unpinned = _parent(other_scope, recipe_revision_id=None)
    payload["node_run_attempt_id"], payload["recipe_revision_id"] = str(unpinned), str(active_revision)
    assert (await _request("POST", "/api/v1/recipes/execute", headers=headers, json=payload)).status_code == 409
    assert _effects(unpinned) == (0, 0)

    local_revision, local_body = _frozen_recipe(other_scope)
    mismatch = _parent(other_scope, recipe_revision_id=local_revision)
    payload.update({"node_run_attempt_id": str(mismatch), "recipe_revision_id": str(active_revision), "body": local_body})
    assert (await _request("POST", "/api/v1/recipes/execute", headers=headers, json=payload)).status_code == 409
    assert _effects(mismatch) == (0, 0)

    inactive_revision, inactive_body = _frozen_recipe(other_scope, active=False)
    inactive = _parent(other_scope, recipe_revision_id=inactive_revision)
    payload.update({"node_run_attempt_id": str(inactive), "recipe_revision_id": str(inactive_revision), "body": inactive_body})
    assert (await _request("POST", "/api/v1/recipes/execute", headers=headers, json=payload)).status_code == 409
    assert _effects(inactive) == (0, 0)


@pytest.mark.asyncio
async def test_recipe_execute_uses_parent_frozen_inputs_not_browser_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.api.routes.recipe as route
    captured: dict = {}

    class FixedAdapter:
        configured = True
        def submit(self, **kwargs: object) -> AtlasSubmission:
            captured.update(kwargs)
            return AtlasSubmission(task_id=None, model_version="atlas/test", outputs=[{"ok": True}], usage={}, actual_cost=0.1, raw_fingerprint="test")

    monkeypatch.setattr(route, "AtlasCloudAdapter", FixedAdapter)
    owner_id, token = await _identity()
    owner_scope = f"user:{owner_id}"
    revision, frozen_body = _frozen_recipe(owner_scope)
    parent = _parent(owner_scope, recipe_revision_id=revision, inputs={"prompt": "fixed"})
    response = await _request("POST", "/api/v1/recipes/execute", headers={"Authorization": f"Bearer {token}"}, json={
        "node_run_attempt_id": str(parent), "recipe_revision_id": str(revision), "idempotency_key": str(uuid4()),
        "inputs": {"prompt": "browser-override"}, "body": {"recipe_type": "evil", "operator_graph": {}},
    })
    assert response.status_code == 200
    assert captured["payload"] == {"input": {"prompt": "fixed"}, "parameters": {}}
