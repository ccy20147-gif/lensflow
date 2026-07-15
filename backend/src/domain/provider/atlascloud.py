"""AtlasCloud HTTP adapter.

The adapter deliberately has a tiny, injectable transport boundary.  It is
safe to exercise with a fake in tests and never returns or logs the API key.
AtlasCloud accepts OpenAI-compatible chat completion requests and asynchronous
image/video predictions; the endpoint map remains configurable because model
families expose different prediction paths.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
    asynchronous: bool = False


class AtlasSubmissionUnknown(SafeError):
    """The TCP/request outcome is unknowable; reconciliation is required."""

    def __init__(self) -> None:
        super().__init__("PROVIDER_SUBMISSION_UNKNOWN", "AtlasCloud 请求状态未知，等待对账", 503)


class AtlasWebhookVerifier:
    """Verify the documented AtlasCloud Ed25519 webhook envelope.

    Keys are short-lived process-local cache entries.  An unknown ``kid``
    forces one refresh, allowing normal Atlas key rotation without accepting a
    key that was never advertised by the official JWKS endpoint.
    """

    JWKS_PATH = "/api/v1/webhooks/jwks.json"
    CACHE_SECONDS = 300
    REPLAY_WINDOW_SECONDS = 300
    _keys: dict[str, bytes] = {}
    _keys_expires_at: float = 0.0

    @classmethod
    def reset_cache(cls) -> None:
        cls._keys = {}
        cls._keys_expires_at = 0.0

    @staticmethod
    def _b64url(value: str) -> bytes:
        try:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid base64url") from exc

    @classmethod
    def _load_keys(cls, *, base_url: str, fetcher: Callable[[str], Any] | None, force: bool = False) -> dict[str, bytes]:
        now = time.monotonic()
        if not force and cls._keys and cls._keys_expires_at > now:
            return cls._keys
        url = f"{base_url.rstrip('/')}{cls.JWKS_PATH}"
        try:
            response = fetcher(url) if fetcher is not None else httpx.get(url, timeout=5.0)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            document = response.json() if hasattr(response, "json") else response
        except Exception as exc:
            # Deliberately do not include endpoint response or headers: either
            # can contain provider diagnostics that should not reach logs/API.
            raise ValueError("unable to refresh AtlasCloud JWKS") from exc
        raw_keys = document.get("keys") if isinstance(document, dict) else None
        parsed: dict[str, bytes] = {}
        if isinstance(raw_keys, list):
            for jwk in raw_keys:
                if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                    continue
                kid, value = jwk.get("kid"), jwk.get("x")
                if not isinstance(kid, str) or not isinstance(value, str):
                    continue
                try:
                    raw = cls._b64url(value)
                except ValueError:
                    continue
                if len(raw) == 32:
                    parsed[kid] = raw
        if not parsed:
            raise ValueError("AtlasCloud JWKS contains no usable Ed25519 key")
        cls._keys = parsed
        cls._keys_expires_at = now + cls.CACHE_SECONDS
        return parsed

    @classmethod
    def verify(
        cls, *, body: bytes, timestamp: str, signature: str, key_id: str,
        base_url: str, fetcher: Callable[[str], Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        if not timestamp or not signature or not key_id:
            return False
        try:
            timestamp_value = float(timestamp)
            current = (now or datetime.now(timezone.utc)).timestamp()
            if abs(current - timestamp_value) > cls.REPLAY_WINDOW_SECONDS:
                return False
            signature_bytes = cls._b64url(signature)
        except (TypeError, ValueError, OverflowError):
            return False
        try:
            keys = cls._load_keys(base_url=base_url, fetcher=fetcher)
            public_bytes = keys.get(key_id)
            if public_bytes is None:
                keys = cls._load_keys(base_url=base_url, fetcher=fetcher, force=True)
                public_bytes = keys.get(key_id)
            if public_bytes is None:
                return False
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                signature_bytes, timestamp.encode("utf-8") + b"." + body,
            )
            return True
        except (InvalidSignature, ValueError):
            return False


def validate_public_webhook_url(value: str) -> str:
    """Reject SSRF-capable callback targets before a media request is sent."""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PolicyBlockedError("AtlasCloud webhook URL 必须是公网 HTTPS 地址")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise PolicyBlockedError("AtlasCloud webhook URL 不允许 localhost")
    try:
        addresses = [item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
    except socket.gaierror as exc:
        raise PolicyBlockedError("AtlasCloud webhook URL 必须可解析到公网地址") from exc
    if not addresses:
        raise PolicyBlockedError("AtlasCloud webhook URL 必须可解析到公网地址")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise PolicyBlockedError("AtlasCloud webhook URL 地址无效") from exc
        if not ip.is_global:
            raise PolicyBlockedError("AtlasCloud webhook URL 不允许私网地址")
    return value


class AtlasCloudAdapter:
    provider_id = "atlascloud"

    def __init__(self, *, transport: AtlasTransport | None = None, api_key: str | None = None,
                 base_url: str | None = None, webhook_url: str | None = None) -> None:
        self._transport = transport or httpx.Client(timeout=settings.atlascloud_timeout_seconds)
        self._api_key = api_key if api_key is not None else settings.atlascloud_api_key
        self._base_url = (base_url or settings.atlascloud_base_url).rstrip("/")
        configured_webhook = settings.atlascloud_webhook_url if webhook_url is None else webhook_url
        self._webhook_url = validate_public_webhook_url(configured_webhook) if configured_webhook else ""

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def submit(self, *, operation: str, model_id: str, payload: dict[str, Any],
               idempotency_key: str) -> AtlasSubmission:
        if not self._api_key:
            raise PolicyBlockedError("AtlasCloud 凭证未配置")
        path = {
            # AtlasCloud documents chat as OpenAI-compatible under /v1,
            # whereas media prediction APIs live under /api/v1/model.
            "llm": "/v1/chat/completions",
            "image": "/api/v1/model/generateImage",
            "video": "/api/v1/model/generateVideo",
        }.get(operation)
        if path is None:
            raise PolicyBlockedError("不允许的 AtlasCloud 操作")
        request_payload = {"model": model_id, **payload}
        if operation in {"image", "video"} and self._webhook_url:
            request_payload["webhook_url"] = self._webhook_url
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
        if operation == "llm":
            # Do not publish an OpenAI choice wrapper as a business artifact.
            # Agent schemas receive the decoded JSON object, or an explicit
            # text object when the model returned ordinary prose.
            outputs = self._decode_chat_outputs(data)
            task_id = None
            asynchronous = False
        else:
            # AtlasCloud media submit responses are acknowledgements, not
            # outputs.  The result may only be published by callback/polling
            # after the task id has been durably bound to this invocation.
            envelope = data.get("data")
            envelope_data = envelope if isinstance(envelope, dict) else {}
            task_id = str(
                envelope_data.get("id") or envelope_data.get("prediction_id")
                or data.get("id") or data.get("prediction_id") or ""
            ) or None
            outputs = []
            asynchronous = task_id is not None
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = data.get("cost", usage.get("cost", 0.0))
        return AtlasSubmission(
            task_id=task_id,
            model_version=str(data.get("model_version") or data.get("model") or model_id), outputs=outputs,
            usage=usage, actual_cost=float(cost or 0.0),
            raw_fingerprint=response.headers.get("x-request-id", ""),
            asynchronous=asynchronous,
        )

    @staticmethod
    def _decode_chat_outputs(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Decode documented OpenAI-compatible choices into typed outputs.

        The legacy ``data`` list remains a test/backward-compatible fallback,
        but production chat handling deliberately does not expose choice,
        message, or provider metadata as application output.
        """
        choices = data.get("choices")
        if not isinstance(choices, list):
            legacy = data.get("data")
            return [item for item in legacy if isinstance(item, dict)] if isinstance(legacy, list) else []
        outputs: list[dict[str, Any]] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            content = message.get("content")
            decoded: dict[str, Any] | None = None
            if isinstance(content, str) and content.strip():
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    decoded = {"text": content}
                else:
                    decoded = parsed if isinstance(parsed, dict) else {"text": content}
            elif isinstance(content, list):
                text = "".join(
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
                if text:
                    decoded = {"text": text}
            if isinstance(tool_calls, list):
                decoded = {**(decoded or {}), "tool_calls": tool_calls}
            if decoded is not None:
                outputs.append(decoded)
        return outputs

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
