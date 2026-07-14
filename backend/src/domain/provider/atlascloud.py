"""AtlasCloud HTTP adapter.

The adapter deliberately has a tiny, injectable transport boundary.  It is
safe to exercise with a fake in tests and never returns or logs the API key.
AtlasCloud accepts OpenAI-compatible chat completion requests and asynchronous
image/video predictions; the endpoint map remains configurable because model
families expose different prediction paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Any, Protocol

import httpx

from src.core.config import settings
from src.core.exceptions import PolicyBlockedError, SafeError


class AtlasTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


@dataclass(frozen=True)
class AtlasSubmission:
    task_id: str | None
    model_version: str
    outputs: list[dict[str, Any]]
    usage: dict[str, Any]
    actual_cost: float
    raw_fingerprint: str


class AtlasSubmissionUnknown(SafeError):
    """The TCP/request outcome is unknowable; reconciliation is required."""

    def __init__(self) -> None:
        super().__init__("PROVIDER_SUBMISSION_UNKNOWN", "AtlasCloud 请求状态未知，等待对账", 503)


class AtlasCloudAdapter:
    provider_id = "atlascloud"

    def __init__(self, *, transport: AtlasTransport | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self._transport = transport or httpx.Client(timeout=settings.atlascloud_timeout_seconds)
        self._api_key = api_key if api_key is not None else settings.atlascloud_api_key
        self._base_url = (base_url or settings.atlascloud_base_url).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def submit(self, *, operation: str, model_id: str, payload: dict[str, Any],
               idempotency_key: str) -> AtlasSubmission:
        if not self._api_key:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        path = {
            "llm": "/api/v1/chat/completions",
            "image": "/api/v1/model/generateImage",
            "video": "/api/v1/model/generateVideo",
        }.get(operation)
        if path is None:
            raise PolicyBlockedError("不允许的 AtlasCloud 操作")
        request_payload = {"model": model_id, **payload}
        try:
            response = self._transport.request(
                "POST", f"{self._base_url}{path}", json=request_payload,
                headers={"Authorization": f"Bearer {self._api_key}", "Idempotency-Key": idempotency_key},
            )
        except httpx.RequestError as exc:
            # A connection error after dispatch cannot prove whether the provider
            # received the request.  The caller marks the durable attempt UNKNOWN.
            raise AtlasSubmissionUnknown() from exc
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise AtlasSubmissionUnknown()
        if response.status_code >= 400:
            raise SafeError("PROVIDER_REJECTED", "AtlasCloud 拒绝请求", 502)
        data = response.json()
        outputs = data.get("outputs") or data.get("data") or data.get("choices") or []
        if not isinstance(outputs, list):
            outputs = [outputs]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = data.get("cost", usage.get("cost", 0.0))
        return AtlasSubmission(
            task_id=str(data.get("id") or data.get("prediction_id") or "") or None,
            model_version=str(data.get("model_version") or model_id), outputs=outputs,
            usage=usage, actual_cost=float(cost or 0.0),
            raw_fingerprint=response.headers.get("x-request-id", ""),
        )

    def get_prediction(self, task_id: str) -> dict[str, Any]:
        if not self._api_key:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        response = self._transport.request(
            "GET", f"{self._base_url}/api/v1/model/prediction/{task_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code >= 400:
            raise SafeError("PROVIDER_RECONCILIATION_FAILED", "AtlasCloud 对账失败", 502)
        return response.json()

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        """Upload an explicit media input through the AtlasCloud file API."""
        if not self._api_key:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        response = self._transport.request(
            "POST", f"{self._base_url}/api/v1/model/uploadMedia",
            files={"file": (filename, content, content_type)},
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code >= 400:
            raise SafeError("PROVIDER_UPLOAD_FAILED", "AtlasCloud 上传失败", 502)
        return response.json()

    @staticmethod
    def verify_webhook(*, body: bytes, signature: str, secret: str) -> bool:
        """Constant-time HMAC verification; callers persist only the result."""
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.removeprefix("sha256="))
