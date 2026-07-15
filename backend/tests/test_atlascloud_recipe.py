"""AtlasCloud and Media Recipe contract tests; no live credential required."""
from __future__ import annotations

import base64
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.core.exceptions import PolicyBlockedError, ValidationError_
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown, AtlasWebhookVerifier
from src.domain.recipe.media_recipe_compiler import compile_media_recipe


class FakeTransport:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _recipe() -> dict:
    return {
        "recipe_type": "image_pipeline",
        "public_input_schema_refs": ["toon.prompt@1"],
        "operator_graph": {
            "source": {"type": "input", "outputs": ["prompt"]},
            "generate": {"type": "atlas_image", "model_id": "image-model-v1", "inputs": ["source.prompt"], "outputs": ["image"], "required_controls": ["pose"], "supported_controls": ["pose"]},
            "convert": {"type": "format_convert", "inputs": ["generate.image"], "outputs": ["png"]},
        },
    }


def test_recipe_compiler_freezes_sorted_dag_and_capabilities() -> None:
    result = compile_media_recipe(_recipe())
    assert [step["id"] for step in result["compiled_plan"]["steps"]] == ["source", "generate", "convert"]
    assert result["compiled_plan"]["steps"][1]["capability_snapshot"]["provider"] == "atlascloud"
    assert result["plan_hash"]


def test_recipe_compiler_rejects_cycles_and_prohibited_operator() -> None:
    cycle = _recipe()
    cycle["operator_graph"]["source"]["inputs"] = ["convert.png"]
    with pytest.raises(ValidationError_):
        compile_media_recipe(cycle)
    forbidden = _recipe()
    forbidden["operator_graph"]["generate"]["type"] = "agent"
    with pytest.raises(PolicyBlockedError):
        compile_media_recipe(forbidden)


def test_recipe_compiler_reports_explicit_control_degrade() -> None:
    recipe = _recipe()
    recipe["operator_graph"]["generate"].update({"supported_controls": [], "unsupported_policy": "degrade"})
    result = compile_media_recipe(recipe)
    assert result["compiled_plan"]["control_outcomes"] == [{"operator_id": "generate", "control": "pose", "outcome": "degraded"}]


def test_atlas_adapter_sends_idempotency_without_exposing_secret() -> None:
    request = httpx.Request("POST", "https://atlas.test/api/v1/model/generateImage")
    transport = FakeTransport(httpx.Response(200, request=request, json={"id": "p-1", "data": [{"url": "https://media"}], "usage": {"cost": 0.12}}))
    adapter = AtlasCloudAdapter(transport=transport, api_key="test-secret", base_url="https://atlas.test")
    result = adapter.submit(operation="image", model_id="model", payload={"prompt": "x"}, idempotency_key="stable-key")
    assert result.task_id == "p-1"
    assert result.actual_cost == 0.12
    _, _, kwargs = transport.calls[0]
    assert kwargs["headers"]["Idempotency-Key"] == "stable-key"  # type: ignore[index]
    assert "test-secret" not in repr(result)


def test_atlas_adapter_adds_configured_public_webhook_to_media() -> None:
    request = httpx.Request("POST", "https://atlas.test/api/v1/model/generateImage")
    transport = FakeTransport(httpx.Response(200, request=request, json={"data": {"id": "p-1"}}))
    adapter = AtlasCloudAdapter(
        transport=transport, api_key="test-secret", base_url="https://atlas.test",
        webhook_url="https://1.1.1.1/atlascloud/webhook",
    )
    adapter.submit(operation="image", model_id="model", payload={"prompt": "x"}, idempotency_key="stable-key")
    assert transport.calls[0][2]["json"]["webhook_url"] == "https://1.1.1.1/atlascloud/webhook"  # type: ignore[index]


def test_atlas_adapter_rejects_private_webhook_target() -> None:
    with pytest.raises(PolicyBlockedError):
        AtlasCloudAdapter(api_key="key", webhook_url="https://127.0.0.1/callback")


def test_atlas_webhook_ed25519_uses_mock_jwks_and_refreshes_unknown_kid() -> None:
    private = Ed25519PrivateKey.generate()
    raw_key = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = "rotated-key"
    body, timestamp = b'{"session_id":"one"}', str(int(time.time()))
    signature = base64.urlsafe_b64encode(private.sign(timestamp.encode() + b"." + body)).decode().rstrip("=")
    calls: list[str] = []

    def fetcher(url: str) -> dict[str, object]:
        calls.append(url)
        return {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": base64.urlsafe_b64encode(raw_key).decode().rstrip("=")}]}

    AtlasWebhookVerifier.reset_cache()
    # First fetch fills the cache, then the rotated kid forces exactly one
    # refresh; no real network is reachable from this test.
    AtlasWebhookVerifier._keys = {"old-key": raw_key}
    AtlasWebhookVerifier._keys_expires_at = time.monotonic() + 60
    assert AtlasWebhookVerifier.verify(body=body, timestamp=timestamp, signature=signature, key_id=kid, base_url="https://atlas.invalid", fetcher=fetcher)
    assert calls == ["https://atlas.invalid/api/v1/webhooks/jwks.json"]
    assert not AtlasWebhookVerifier.verify(body=body, timestamp=str(int(time.time()) - 301), signature=signature, key_id=kid, base_url="https://atlas.invalid", fetcher=fetcher)


def test_atlas_adapter_uses_documented_chat_path_and_decodes_json_content() -> None:
    request = httpx.Request("POST", "https://atlas.test/v1/chat/completions")
    transport = FakeTransport(httpx.Response(
        200, request=request,
        json={"model": "deepseek-v3", "choices": [{"message": {"content": '{"answer":"ok"}'}}]},
    ))
    result = AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test").submit(
        operation="llm", model_id="deepseek-v3", payload={"messages": []}, idempotency_key="stable-key",
    )
    assert transport.calls[0][1] == "https://atlas.test/v1/chat/completions"
    assert result.outputs == [{"answer": "ok"}]
    assert result.task_id is None and not result.asynchronous


def test_atlas_adapter_decodes_chat_text_without_choice_wrapper() -> None:
    request = httpx.Request("POST", "https://atlas.test/v1/chat/completions")
    transport = FakeTransport(httpx.Response(
        200, request=request,
        json={"choices": [{"message": {"content": "plain response"}}]},
    ))
    result = AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test").submit(
        operation="llm", model_id="deepseek-v3", payload={"messages": []}, idempotency_key="stable-key",
    )
    assert result.outputs == [{"text": "plain response"}]


def test_atlas_adapter_reads_documented_async_data_envelope() -> None:
    request = httpx.Request("POST", "https://atlas.test/api/v1/model/generateImage")
    transport = FakeTransport(httpx.Response(
        200, request=request,
        json={"code": 200, "data": {"id": "prediction-1", "status": "processing"}},
    ))
    result = AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test").submit(
        operation="image", model_id="seedream-3.0", payload={"prompt": "x"}, idempotency_key="stable-key",
    )
    assert result.task_id == "prediction-1"
    assert result.outputs == [] and result.asynchronous


def test_atlas_adapter_marks_transport_ambiguity_unknown() -> None:
    transport = FakeTransport(httpx.ConnectError("network"))
    adapter = AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test")
    with pytest.raises(AtlasSubmissionUnknown):
        adapter.submit(operation="video", model_id="model", payload={}, idempotency_key="k")


def test_atlas_upload_and_webhook_contract() -> None:
    request = httpx.Request("POST", "https://atlas.test/api/v1/model/uploadMedia")
    transport = FakeTransport(httpx.Response(200, request=request, json={"id": "file-1"}))
    adapter = AtlasCloudAdapter(transport=transport, api_key="key", base_url="https://atlas.test")
    assert adapter.upload_file(filename="input.png", content=b"png", content_type="image/png")["id"] == "file-1"
    import hashlib
    import hmac
    signature = hmac.new(b"hook", b"payload", hashlib.sha256).hexdigest()
    assert AtlasCloudAdapter.verify_webhook(body=b"payload", signature=signature, secret="hook")
    assert not AtlasCloudAdapter.verify_webhook(body=b"payload", signature="bad", secret="hook")


def test_atlas_adapter_requires_configured_secret() -> None:
    with pytest.raises(PolicyBlockedError):
        AtlasCloudAdapter(api_key="").submit(operation="llm", model_id="m", payload={}, idempotency_key="k")
