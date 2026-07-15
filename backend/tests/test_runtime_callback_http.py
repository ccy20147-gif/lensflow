"""HTTP contract for signed AtlasCloud callbacks (TF-WF-006)."""
from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from src.domain.runtime.runtime_service import RuntimeService
from src.infra.db.models import (
    NodeRunAttemptModel,
    NodeRunModel,
    ProviderInvocationRecordModel,
    WorkflowModel,
    WorkflowRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1 to run against PostgreSQL",
)


@pytest.mark.asyncio
async def test_atlas_callback_ed25519_unknown_and_duplicate_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a signed callback for a durable task binding can publish once."""
    factory = get_session_factory()
    workflow_id, revision_id, owner_id = uuid4(), uuid4(), uuid4()
    with factory.begin() as session:
        session.add(WorkflowModel(workflow_id=workflow_id, owner_scope=f"user:{owner_id}"))
        session.add(WorkflowRevisionModel(revision_id=revision_id, workflow_id=workflow_id, revision_number=1,
            graph_hash="g", execution_hash="e", registry_snapshot_id=uuid4(), revision_status=RevisionStatus.ACTIVE))
    plan = CompiledExecutionPlan(plan_id=uuid4(), workflow_revision_id=revision_id,
        registry_snapshot=RegistrySnapshot(snapshot_id=uuid4()),
        resolved_graph={"nodes": [{"id": "provider", "type": "provider"}], "edges": []}, plan_hash="callback")
    runtime = RuntimeService(factory)
    run = runtime.create_run(compiled_plan=plan, owner_scope=OwnerScope(kind="user", id=owner_id))
    with factory() as session:
        node = session.scalar(select(NodeRunModel).where(NodeRunModel.run_id == run.run_id))
        assert node is not None
        attempt = session.scalar(select(NodeRunAttemptModel).where(NodeRunAttemptModel.node_run_id == node.node_run_id))
        assert attempt is not None
    provider, _ = runtime.dispatch_provider(attempt.attempt_id, provider_id="atlascloud", model_id="test", idempotency_key=str(uuid4()), request_body_hash="h")
    task_id = f"callback-{uuid4()}"
    runtime.bind_provider_task(provider.provider_attempt_id, task_id)

    # Delayed import isolates the runtime PG module from the full composition
    # root during collection; ASGITransport exercises actual HTTP middleware.
    from src.app import app
    from src.domain.provider.atlascloud import AtlasWebhookVerifier
    payload = {
        "session_id": task_id, "event_type": "prediction.updated", "status": "OK",
        "payload": {"status": "completed", "outputs": [{"text": "ok"}], "model_version": "test"},
    }
    raw = json.dumps(payload).encode()
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(AtlasWebhookVerifier, "_load_keys", classmethod(lambda cls, **_kwargs: {"test-kid": public}))
    timestamp = str(int(time.time()))
    signed = __import__("base64").urlsafe_b64encode(private.sign(timestamp.encode() + b"." + raw)).decode().rstrip("=")
    unknown_payload = {**payload, "session_id": "unknown-task"}
    unknown_raw = json.dumps(unknown_payload).encode()
    unknown_signed = __import__("base64").urlsafe_b64encode(private.sign(timestamp.encode() + b"." + unknown_raw)).decode().rstrip("=")
    headers = {
        "X-AtlasCloud-Webhook-Id": task_id, "X-AtlasCloud-Event": "prediction.updated",
        "X-AtlasCloud-Timestamp": timestamp, "X-AtlasCloud-Signature-Ed25519": signed,
        "X-AtlasCloud-Key-Id": "test-kid",
    }
    unknown_headers = {**headers, "X-AtlasCloud-Webhook-Id": "unknown-task", "X-AtlasCloud-Signature-Ed25519": unknown_signed}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://toonflow.test") as client:
        assert (await client.post("/api/v1/runtime/callbacks/atlascloud", content=raw, headers={**headers, "X-AtlasCloud-Signature-Ed25519": "wrong"})).status_code == 401
        assert (await client.post("/api/v1/runtime/callbacks/atlascloud", content=unknown_raw, headers=unknown_headers)).status_code == 404
        assert (await client.post("/api/v1/runtime/callbacks/atlascloud", content=raw, headers=headers)).status_code == 200
        assert (await client.post("/api/v1/runtime/callbacks/atlascloud", content=raw, headers=headers)).status_code == 200
    with factory() as session:
        records = list(session.scalars(select(ProviderInvocationRecordModel).where(ProviderInvocationRecordModel.provider_attempt_id == provider.provider_attempt_id)))
        assert len(records) == 1
