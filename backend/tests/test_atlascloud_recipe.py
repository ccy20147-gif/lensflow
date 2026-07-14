"""AtlasCloud and Media Recipe contract tests; no live credential required."""
from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import PolicyBlockedError, ValidationError_
from src.domain.provider.atlascloud import AtlasCloudAdapter, AtlasSubmissionUnknown
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
