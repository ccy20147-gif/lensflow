"""Opt-in AtlasCloud live smoke checks.

This module makes real network calls only when both ``ATLASCLOUD_API_KEY``
and ``ATLASCLOUD_LIVE_SMOKE_ENABLED=1`` are supplied.  It never prints API
keys, prompts, response bodies, temporary upload URLs, or prediction IDs.
Video submission has the additional ``ATLASCLOUD_LIVE_SMOKE_VIDEO_ENABLED=1``
confirmation because it may incur a materially higher cost.

AtlasCloud documents these endpoints at /docs/models/{llm,image,video},
/docs/predictions, and /docs/upload-files.  Models and payloads are overrideable
for accounts with different enabled catalogues; the defaults are deliberately
small and bounded rather than application production defaults.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any
from uuid import uuid4

import httpx
import pytest

from src.domain.provider.atlascloud import AtlasCloudAdapter


_ENABLED = (
    os.environ.get("ATLASCLOUD_LIVE_SMOKE_ENABLED") == "1"
    and bool(os.environ.get("ATLASCLOUD_API_KEY"))
)
pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="requires ATLASCLOUD_API_KEY and ATLASCLOUD_LIVE_SMOKE_ENABLED=1",
)


def _timeout_seconds() -> float:
    raw = os.environ.get("ATLASCLOUD_LIVE_SMOKE_TIMEOUT_SECONDS", "30")
    try:
        return min(60.0, max(1.0, float(raw)))
    except ValueError as exc:
        raise ValueError("ATLASCLOUD_LIVE_SMOKE_TIMEOUT_SECONDS must be a number") from exc


def _payload(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        supplied = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(supplied, dict):
        raise ValueError(f"{name} must be a JSON object")
    return supplied


@pytest.fixture(scope="module")
def adapter() -> AtlasCloudAdapter:
    # The API key is read by the adapter and is never included in assertions or
    # test output.  Each request gets a short, bounded client timeout.
    return AtlasCloudAdapter(transport=httpx.Client(timeout=_timeout_seconds()))


def _idempotency_key() -> str:
    return f"toonflow-live-smoke-{uuid4()}"


def test_live_chat_completion(adapter: AtlasCloudAdapter) -> None:
    submission = adapter.submit(
        operation="llm",
        model_id=os.environ.get("ATLASCLOUD_LIVE_SMOKE_CHAT_MODEL", "deepseek-v3"),
        payload=_payload("ATLASCLOUD_LIVE_SMOKE_CHAT_PAYLOAD_JSON", {
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 4,
            "temperature": 0,
        }),
        idempotency_key=_idempotency_key(),
    )
    assert isinstance(submission.outputs, list)


def test_live_upload_media(adapter: AtlasCloudAdapter) -> None:
    # A valid one-pixel PNG keeps transfer/storage cost negligible.
    one_pixel_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
        "zC3+WQAAAABJRU5ErkJggg=="
    )
    result = adapter.upload_file(filename="toonflow-live-smoke.png", content=one_pixel_png, content_type="image/png")
    assert isinstance(result, dict)


def test_live_image_submission_and_prediction_poll(adapter: AtlasCloudAdapter) -> None:
    submission = adapter.submit(
        operation="image",
        model_id=os.environ.get("ATLASCLOUD_LIVE_SMOKE_IMAGE_MODEL", "seedream-3.0"),
        payload=_payload("ATLASCLOUD_LIVE_SMOKE_IMAGE_PAYLOAD_JSON", {
            "prompt": "A single blue dot on a plain white background.",
        }),
        idempotency_key=_idempotency_key(),
    )
    assert submission.task_id is not None
    prediction = adapter.get_prediction(submission.task_id)
    assert isinstance(prediction, dict)


def test_live_video_submission_and_prediction_poll(adapter: AtlasCloudAdapter) -> None:
    if os.environ.get("ATLASCLOUD_LIVE_SMOKE_VIDEO_ENABLED") != "1":
        pytest.skip("set ATLASCLOUD_LIVE_SMOKE_VIDEO_ENABLED=1 to authorize billable video smoke")
    submission = adapter.submit(
        operation="video",
        model_id=os.environ.get(
            "ATLASCLOUD_LIVE_SMOKE_VIDEO_MODEL",
            "alibaba/wan-2.1/t2v-480p-ultra-fast",
        ),
        payload=_payload("ATLASCLOUD_LIVE_SMOKE_VIDEO_PAYLOAD_JSON", {
            "prompt": "A single blue dot moves slowly across a plain white background.",
            "duration": 5,
            "resolution": "480p",
        }),
        idempotency_key=_idempotency_key(),
    )
    assert submission.task_id is not None
    prediction = adapter.get_prediction(submission.task_id)
    assert isinstance(prediction, dict)
