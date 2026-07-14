"""HTTP-level bearer ownership contracts; no client owner fields are trusted."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import httpx
import pytest

from src.app import app
from src.core.config import settings
from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.models import ArtifactVersionModel, NodeRunAttemptModel, NodeRunModel, WorkflowRunModel
from src.schemas.enums import AttemptStatus, NodeRunStatus, RunStatus
from src.schemas.models import NodeDefinitionRevision, OwnerScope

pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


async def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def _identity() -> tuple[str, str]:
    email = f"p0-{uuid4()}@test.local"
    await _request("POST", "/api/v1/identity/register", json={"email": email, "display_name": "P0", "password": "Correct-Password-1"})
    login = await _request("POST", "/api/v1/identity/login", json={"email": email, "password": "Correct-Password-1"})
    body = login.json()
    return body["account_id"], body["token"]


@pytest.mark.asyncio
async def test_workflow_and_artifact_owner_are_derived_from_bearer() -> None:
    first_id, first_token = await _identity()
    second_id, second_token = await _identity()
    headers = {"Authorization": f"Bearer {first_token}"}
    created = await _request("POST", "/api/v1/workflows/", headers=headers, json={"owner_kind": "user", "owner_id": second_id})
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]
    assert created.json()["owner_scope"] == f"user:{first_id}"
    denied = await _request("GET", f"/api/v1/workflows/{workflow_id}/draft", headers={"Authorization": f"Bearer {second_token}"})
    assert denied.status_code == 404
    artifact = await _request("POST", "/api/v1/artifacts/versions", headers=headers, json={"schema_id": "test", "owner_id": second_id, "content_json": {"x": 1}})
    assert artifact.status_code == 200
    version_id = artifact.json()["artifact_version_id"]
    assert artifact.json()["owner_scope"]["id"] == first_id
    denied_artifact = await _request("GET", f"/api/v1/artifacts/versions/{version_id}", headers={"Authorization": f"Bearer {second_token}"})
    assert denied_artifact.status_code == 403


@pytest.mark.asyncio
async def test_private_template_detail_and_instantiation_are_owner_only() -> None:
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    workflows = SqlWorkflowService()
    workflow = workflows.create_workflow(owner_scope=owner)
    revision = workflows.create_revision_from_draft(workflow.workflow_id, uuid4())
    created = await _request("POST", "/api/v1/templates", headers={"Authorization": f"Bearer {owner_token}"}, json={"name": "private", "workflow_revision_id": str(revision.revision_id), "visibility": "private"})
    assert created.status_code == 201
    template_id = created.json()["template_id"]
    assert (await _request("GET", f"/api/v1/templates/{template_id}", headers={"Authorization": f"Bearer {owner_token}"})).status_code == 200
    assert (await _request("GET", f"/api/v1/templates/{template_id}", headers={"Authorization": f"Bearer {other_token}"})).status_code == 404
    assert (await _request("PATCH", f"/api/v1/templates/{template_id}", headers={"Authorization": f"Bearer {other_token}"}, json={"name": "stolen"})).status_code == 404
    assert (await _request("PATCH", f"/api/v1/templates/{template_id}", headers={"Authorization": f"Bearer {owner_token}"}, json={"description": "updated"})).status_code == 200
    assert (await _request("POST", f"/api/v1/templates/{template_id}/instantiate", headers={"Authorization": f"Bearer {other_token}"}, json={})).status_code == 404


@pytest.mark.asyncio
async def test_legacy_public_run_endpoint_is_not_exposed() -> None:
    response = await _request("POST", "/api/v1/runtime/runs", json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_registry_mutations_fail_closed_and_require_platform_key() -> None:
    """A bearer/user request must never turn an arbitrary definition active."""
    from src.schemas.models import PortTypeRef

    definition = NodeDefinitionRevision(
        node_type_id=f"test.registry.{uuid4()}", revision_id=uuid4(), semantic_version="1.0.0",
        input_ports=[], output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="test", schema_version=1, cardinality="optional")],
        config_schema={"type": "object"}, executor_ref="workflow.test.registry",
        policy_metadata={"package_source": "approved:http-contract"},
    )
    previous = settings.registry_internal_admin_key
    settings.registry_internal_admin_key = "registry-test-key"
    try:
        body = definition.model_dump(mode="json")
        assert (await _request("POST", "/api/v1/registry/definitions", json=body)).status_code == 403
        created = await _request("POST", "/api/v1/registry/definitions", headers={"X-Registry-Admin-Key": "registry-test-key"}, json=body)
        assert created.status_code == 200
        activated = await _request("POST", f"/api/v1/registry/definitions/{definition.node_type_id}/activate", headers={"X-Registry-Admin-Key": "registry-test-key"}, json={"revision_id": str(definition.revision_id)})
        assert activated.status_code == 200
    finally:
        settings.registry_internal_admin_key = previous


@pytest.mark.asyncio
async def test_control_flow_state_is_owner_scoped() -> None:
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    workflow = SqlWorkflowService().create_workflow(owner_scope=owner)
    revision = SqlWorkflowService().create_revision_from_draft(workflow.workflow_id, uuid4())
    run_id = uuid4()
    from src.infra.db.session import get_session_factory
    with get_session_factory().begin() as session:
        session.add(WorkflowRunModel(run_id=run_id, workflow_revision_id=revision.revision_id,
            compiled_plan_id=uuid4(), owner_scope=owner.scoped_id))
    assert (await _request("GET", f"/api/v1/control-flow/runs/{run_id}/state", headers={"Authorization": f"Bearer {owner_token}"})).status_code == 200
    assert (await _request("GET", f"/api/v1/control-flow/runs/{run_id}/state", headers={"Authorization": f"Bearer {other_token}"})).status_code == 404


@pytest.mark.asyncio
async def test_control_flow_mutations_and_records_are_owner_scoped() -> None:
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    workflows = SqlWorkflowService()
    workflow = workflows.create_workflow(owner_scope=owner)
    revision = workflows.create_revision_from_draft(workflow.workflow_id, uuid4())
    run_id = uuid4()
    from src.infra.db.session import get_session_factory
    with get_session_factory().begin() as session:
        session.add(WorkflowRunModel(run_id=run_id, workflow_revision_id=revision.revision_id,
            compiled_plan_id=uuid4(), owner_scope=owner.scoped_id))
    payload = {"run_id": str(run_id), "node_instance_id": "gate", "operator": "exists"}
    assert (await _request("POST", "/api/v1/control-flow/conditions", json=payload)).status_code == 401
    denied = await _request("POST", "/api/v1/control-flow/conditions", headers={"Authorization": f"Bearer {other_token}"}, json=payload)
    assert denied.status_code == 404
    created = await _request("POST", "/api/v1/control-flow/conditions", headers={"Authorization": f"Bearer {owner_token}"}, json=payload)
    assert created.status_code == 201
    condition_id = created.json()["condition_id"]
    assert (await _request("GET", f"/api/v1/control-flow/conditions/{condition_id}")).status_code == 401
    assert (await _request("POST", f"/api/v1/control-flow/conditions/{condition_id}/evaluate", headers={"Authorization": f"Bearer {other_token}"}, json={"resolved_value": True})).status_code == 404
    assert (await _request("POST", f"/api/v1/control-flow/conditions/{condition_id}/evaluate", headers={"Authorization": f"Bearer {owner_token}"}, json={"resolved_value": True})).status_code == 200


@pytest.mark.asyncio
async def test_workflow_run_cancel_is_owner_scoped_and_stops_scheduling() -> None:
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    workflow = SqlWorkflowService().create_workflow(owner_scope=owner)
    revision = SqlWorkflowService().create_revision_from_draft(workflow.workflow_id, uuid4())
    run_id, node_id, attempt_id = uuid4(), uuid4(), uuid4()
    from src.infra.db.session import get_session_factory
    with get_session_factory().begin() as session:
        session.add(WorkflowRunModel(
            run_id=run_id, workflow_revision_id=revision.revision_id,
            compiled_plan_id=uuid4(), owner_scope=owner.scoped_id, status=RunStatus.RUNNING,
        ))
        session.flush()
        session.add(NodeRunModel(
            node_run_id=node_id, run_id=run_id, node_instance_id="pending",
            node_type_id="provider", status=NodeRunStatus.PENDING,
        ))
        session.flush()
        session.add(NodeRunAttemptModel(
            attempt_id=attempt_id, node_run_id=node_id, status=AttemptStatus.PENDING,
        ))
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    assert (await _request("POST", f"/api/v1/runtime/workflow-runs/{run_id}/cancel", headers={"Authorization": f"Bearer {other_token}"})).status_code == 404
    cancelled = await _request("POST", f"/api/v1/runtime/workflow-runs/{run_id}/cancel", headers=owner_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == RunStatus.CANCELLED.value
    with get_session_factory()() as session:
        attempt = session.get(NodeRunAttemptModel, attempt_id)
        assert attempt is not None and attempt.status == AttemptStatus.CANCELLED
    assert (await _request("POST", f"/api/v1/runtime/workflow-runs/{run_id}/cancel", headers=owner_headers)).status_code == 409


@pytest.mark.asyncio
async def test_typed_sop_trace_lineage_lists_without_500_or_cross_owner_leak() -> None:
    """Agent traces use NodeRunAttempt provenance, not an ArtifactRef."""
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    trace_id, foreign_id, attempt_id = uuid4(), uuid4(), uuid4()
    from src.infra.db.session import get_session_factory
    with get_session_factory().begin() as session:
        session.add_all([
            ArtifactVersionModel(
                artifact_version_id=trace_id, artifact_id=uuid4(), schema_id="toonflow.agent_sop_trace",
                schema_version=1, owner_scope=f"user:{owner_id}", content_json={"phase": "waiting_user"},
                content_hash="owner-trace", content_uri="", blob_uri="",
                lineage_input_refs=[{"node_run_attempt_id": str(attempt_id)}], metadata_json={},
            ),
            ArtifactVersionModel(
                artifact_version_id=foreign_id, artifact_id=uuid4(), schema_id="toonflow.agent_sop_trace",
                schema_version=1, owner_scope=f"user:{uuid4()}", content_json={"secret": "not visible"},
                content_hash="foreign-trace", content_uri="", blob_uri="",
                lineage_input_refs=[{"node_run_attempt_id": str(uuid4())}], metadata_json={},
            ),
        ])
    headers = {"Authorization": f"Bearer {owner_token}"}
    listed = await _request("GET", "/api/v1/artifacts/versions?schema_id=toonflow.agent_sop_trace", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["artifact_version_id"] for row in rows] == [str(trace_id)]
    assert rows[0]["lineage_input_refs"] == [{"node_run_attempt_id": str(attempt_id)}]
    lineage = await _request("GET", f"/api/v1/artifacts/versions/{trace_id}/lineage", headers=headers)
    assert lineage.status_code == 200
    assert lineage.json()["input_refs"] == []
    assert lineage.json()["typed_refs"] == [{"node_run_attempt_id": str(attempt_id)}]
    assert (await _request("GET", f"/api/v1/artifacts/versions/{foreign_id}", headers={"Authorization": f"Bearer {other_token}"})).status_code == 403


@pytest.mark.asyncio
async def test_template_rejects_invalid_pinned_revision_with_structured_diagnostic() -> None:
    _, token = await _identity()
    response = await _request(
        "POST", "/api/v1/templates", headers={"Authorization": f"Bearer {token}"},
        json={"name": "invalid-source", "workflow_revision_id": "not-a-uuid"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]["error"]
    assert detail["code"] == "VALIDATION_ERROR"
    assert detail["details"]["diagnostics"][0]["code"] == "INVALID_UUID"


@pytest.mark.asyncio
async def test_template_instance_and_resource_mutations_are_owner_scoped() -> None:
    """Every template/resource write and instance read derives owner from bearer."""
    owner_id, owner_token = await _identity()
    other_id, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    # Template instantiation re-runs compiler/policy preflight.  Give the
    # source revision a real approved snapshot and a legal public graph,
    # rather than weakening the runtime check for the authorization test.
    ensure_public_business_node_baseline()
    snapshot = SqlRegistryService().freeze_snapshot()
    assert "brief" in snapshot.node_definitions
    workflows = SqlWorkflowService()
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(
        workflow.workflow_id,
        {"nodes": [{"id": "brief", "type": "brief", "config": {}}], "edges": []},
        {},
        {},
        draft.graph_hash,
    )
    revision = workflows.create_revision_from_draft(workflow.workflow_id, snapshot.snapshot_id)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    template = await _request(
        "POST", "/api/v1/templates", headers=owner_headers,
        json={"name": "public-instance", "workflow_revision_id": str(revision.revision_id), "visibility": "public"},
    )
    assert template.status_code == 201
    instance = await _request("POST", f"/api/v1/templates/{template.json()['template_id']}/instantiate", headers=owner_headers, json={})
    assert instance.status_code == 201
    assert (await _request("GET", f"/api/v1/templates/instances/{instance.json()['instance_id']}", headers=other_headers)).status_code == 404

    artifact = await _request("POST", "/api/v1/artifacts/versions", headers=owner_headers, json={"schema_id": "test/resource", "content_json": {"x": 1}})
    assert artifact.status_code == 200
    resource = await _request("POST", "/api/v1/artifacts/resources", headers=owner_headers, json={"resource_type": "world", "content_artifact_version_id": artifact.json()["artifact_version_id"]})
    assert resource.status_code == 201
    resource_id = resource.json()["resource_id"]
    assert (await _request("GET", f"/api/v1/artifacts/resources/{resource_id}/draft", headers=other_headers)).status_code == 403
    assert (await _request("PUT", f"/api/v1/artifacts/resources/{resource_id}/draft", headers=other_headers, json={"content_artifact_version_id": artifact.json()["artifact_version_id"], "base_draft_version": 1})).status_code == 403
    frozen = await _request("POST", f"/api/v1/artifacts/resources/{resource_id}/revisions", headers=owner_headers, json={"base_draft_version": 1})
    assert frozen.status_code == 201
    revision_id = frozen.json()["revision_id"]
    assert (await _request("POST", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/grants", headers=other_headers, json={"grantee_account_id": other_id, "capability_actions": ["reference"]})).status_code == 403
    assert (await _request("POST", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/resolve-ref", headers=other_headers)).status_code == 403


@pytest.mark.asyncio
async def test_oc_elevation_and_external_blob_barrier_are_canonical_api_contracts() -> None:
    """HTTP callers cannot publish pending blobs or forge World-local OC roots."""
    _, token = await _identity()
    headers = {"Authorization": f"Bearer {token}"}
    pending = await _request(
        "POST", "/api/v1/artifacts/versions", headers=headers,
        json={"schema_id": "toonflow.world.v1", "content_uri": "s3://pending/world", "content_hash": ""},
    )
    assert pending.status_code == 422
    embedded_oc = {
        "world_local_character_id": "characters.oc-1",
        "name": "OC",
        "identity_core": {"role": "lead"},
    }
    receipt = {
        "blob_id": "blob://world",
        "checksum": "world-hash",
        "durability_class": "replicated",
        "checkpoint": "journal-2026-07-14T00:00:00Z",
        "protected_at": "2026-07-14T00:00:00Z",
        "restore_point_eligible": True,
        "verified": True,
    }
    forged_confirmation = await _request(
        "POST", "/api/v1/artifacts/versions", headers=headers,
        json={
            "schema_id": "toonflow.world.v1", "content_uri": "s3://forged/world",
            "content_hash": "world-hash", "metadata": {"durability": "confirmed"},
        },
    )
    assert forged_confirmation.status_code == 422
    world_artifact = await _request(
        "POST", "/api/v1/artifacts/versions", headers=headers,
        json={
            "schema_id": "toonflow.world.v1", "content_uri": "s3://durable/world",
            "blob_uri": "s3://durable/blob/world", "content_hash": "world-hash",
            "metadata": {"durability_receipt": receipt},
            "content_json": {"embedded_characters": [embedded_oc]},
        },
    )
    assert world_artifact.status_code == 200
    world = await _request(
        "POST", "/api/v1/artifacts/resources", headers=headers,
        json={"resource_type": "world", "content_artifact_version_id": world_artifact.json()["artifact_version_id"]},
    )
    assert world.status_code == 201
    world_revision = await _request(
        "POST", f"/api/v1/artifacts/resources/{world.json()['resource_id']}/revisions", headers=headers,
        json={"base_draft_version": 1},
    )
    assert world_revision.status_code == 201
    character_artifact = await _request(
        "POST", "/api/v1/artifacts/versions", headers=headers,
        json={"schema_id": "toonflow.character.v1", "content_json": embedded_oc},
    )
    elevated = await _request(
        "POST", "/api/v1/artifacts/resources/elevate-oc", headers=headers,
        json={
            "content_artifact_version_id": character_artifact.json()["artifact_version_id"],
            "source_world_revision_id": world_revision.json()["revision_id"],
            "source_local_id": "characters.oc-1",
        },
    )
    assert elevated.status_code == 201
    character_revision = await _request(
        "POST", f"/api/v1/artifacts/resources/{elevated.json()['resource_id']}/revisions", headers=headers,
        json={"base_draft_version": 1},
    )
    assert character_revision.status_code == 201
    provenance = await _request(
        "GET", f"/api/v1/artifacts/resources/{elevated.json()['resource_id']}/provenance", headers=headers,
    )
    assert provenance.status_code == 200
    assert provenance.json()["revisions"][0]["source_world_revision_id"] == world_revision.json()["revision_id"]
    assert provenance.json()["revisions"][0]["source_local_id"] == "characters.oc-1"
    assert provenance.json()["revisions"][0]["source_content_hash"]

    forged = await _request(
        "POST", "/api/v1/artifacts/versions", headers=headers,
        json={"schema_id": "toonflow.character.v1", "content_json": {"world_local_character_id": "characters.oc-1", "name": "forged"}},
    )
    assert forged.status_code == 200
    denied = await _request(
        "POST", "/api/v1/artifacts/resources/elevate-oc", headers=headers,
        json={
            "content_artifact_version_id": forged.json()["artifact_version_id"],
            "source_world_revision_id": world_revision.json()["revision_id"],
            "source_local_id": "characters.oc-1",
        },
    )
    assert denied.status_code == 409


@pytest.mark.asyncio
async def test_template_gallery_does_not_leak_private_packages() -> None:
    owner_id, owner_token = await _identity()
    _, other_token = await _identity()
    owner = OwnerScope(kind="user", id=UUID(owner_id))
    workflows = SqlWorkflowService()
    workflow = workflows.create_workflow(owner_scope=owner)
    revision = workflows.create_revision_from_draft(workflow.workflow_id, uuid4())
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    created = await _request(
        "POST", "/api/v1/templates", headers=owner_headers,
        json={"name": "owner-only-gallery", "workflow_revision_id": str(revision.revision_id), "visibility": "private"},
    )
    assert created.status_code == 201
    template_id = created.json()["template_id"]
    assert template_id in {row["template_id"] for row in (await _request("GET", "/api/v1/templates", headers=owner_headers)).json()}
    assert template_id not in {row["template_id"] for row in (await _request("GET", "/api/v1/templates", headers=other_headers)).json()}


@pytest.mark.asyncio
async def test_resource_grant_actions_and_revoke_block_new_cross_owner_resolution() -> None:
    owner_id, owner_token = await _identity()
    other_id, other_token = await _identity()
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}
    artifact = await _request(
        "POST", "/api/v1/artifacts/versions", headers=owner_headers,
        json={"schema_id": "test/licensed-skill", "content_json": {"instructions": ["x"]}},
    )
    assert artifact.status_code == 200
    resource = await _request(
        "POST", "/api/v1/artifacts/resources", headers=owner_headers,
        json={"resource_type": "skill", "content_artifact_version_id": artifact.json()["artifact_version_id"]},
    )
    assert resource.status_code == 201
    resource_id = resource.json()["resource_id"]
    frozen = await _request(
        "POST", f"/api/v1/artifacts/resources/{resource_id}/revisions", headers=owner_headers,
        json={"base_draft_version": 1},
    )
    assert frozen.status_code == 201
    revision_id = frozen.json()["revision_id"]
    grant = await _request(
        "POST", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/grants",
        headers=owner_headers,
        json={"grantee_account_id": other_id, "capability_actions": ["reference", "execute"]},
    )
    assert grant.status_code == 201
    grant_id = grant.json()["grant_snapshot_id"]
    assert (await _request(
        "POST", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/resolve-ref",
        headers=other_headers, params={"grant_snapshot_id": grant_id},
    )).status_code == 200
    revoked = await _request(
        "DELETE", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/grants/{grant_id}",
        headers=owner_headers,
    )
    assert revoked.status_code == 200
    assert (await _request(
        "POST", f"/api/v1/artifacts/resources/{resource_id}/revisions/{revision_id}/resolve-ref",
        headers=other_headers, params={"grant_snapshot_id": grant_id},
    )).status_code == 403
