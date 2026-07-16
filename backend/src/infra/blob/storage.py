"""Storage adapters for Blob content (TF-OPS-003 Foundation).

The Foundation contract only requires a *pluggable* adapter with a
minimal surface: ``put / get / exists / delete / size / checksum``.  No
real resumable upload is in scope yet — that is V0 per the slice matrix
in ``TF-OPS-003 §6``.

The ``LocalFileBlobAdapter`` writes content under ``settings.blob_storage_path``
in a hash-and-owner-namespaced layout.  ``storage_key`` is intentionally
opaque; it is never returned through the public BlobRef shape.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
from pathlib import Path
from typing import IO, Protocol
from uuid import UUID

from src.core.config import settings
from src.core.exceptions import ValidationError_


class BlobStorageAdapter(Protocol):
    """Minimal contract every backend adapter must satisfy."""

    def put(self, key: str, stream: IO[bytes]) -> int:
        ...

    def get(self, key: str) -> bytes:
        ...

    def exists(self, key: str) -> bool:
        ...

    def size(self, key: str) -> int:
        ...

    def checksum(self, key: str) -> str:
        ...

    def delete(self, key: str) -> None:
        ...

    def move(self, source_key: str, target_key: str) -> str:
        ...


def _safe_segment(value: str) -> str:
    """Reject any value that would let a caller walk out of the bucket."""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    if not cleaned or cleaned.startswith("."):
        raise ValidationError_("storage key segment is unsafe")
    return cleaned


class LocalFileBlobAdapter:
    """Filesystem-backed Blob storage.

    Layout::

        <root>/<owner_kind>/<owner_id>/<blob_id>/<storage_key>

    ``storage_key`` is a free-form opaque string but is sanitised so a
    caller-supplied value cannot escape the bucket.  The contents are the
    raw bytes; the adapter is responsible only for the ``put / get /
    exists / delete / move / checksum`` contract.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(root or settings.blob_storage_path)
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        root_resolved = self._root.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            raise ValidationError_("storage key escapes bucket") from exc
        return candidate

    def _validate_key(self, key: str) -> None:
        if not key or ".." in key.split("/"):
            raise ValidationError_("storage key is unsafe")

    # ------------------------------------------------------------------
    # Adapter surface
    # ------------------------------------------------------------------

    def put(self, key: str, stream: IO[bytes]) -> int:
        self._validate_key(key)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        size = 0
        with path.open("wb") as out:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
                size += len(chunk)
        # Persist the checksum alongside the body so ``checksum`` does not
        # have to re-stream gigabytes just to validate a referenced key.
        path.with_suffix(path.suffix + ".sha256").write_text(hasher.hexdigest(), encoding="utf-8")
        return size

    def put_bytes(self, key: str, payload: bytes) -> int:
        return self.put(key, io.BytesIO(payload))

    def get(self, key: str) -> bytes:
        self._validate_key(key)
        path = self._path(key)
        if not path.exists():
            raise ValidationError_("blob object missing", details={"code": "BLOB_MISSING"})
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        self._validate_key(key)
        return self._path(key).exists()

    def size(self, key: str) -> int:
        self._validate_key(key)
        path = self._path(key)
        if not path.exists():
            raise ValidationError_("blob object missing", details={"code": "BLOB_MISSING"})
        return path.stat().st_size

    def checksum(self, key: str) -> str:
        self._validate_key(key)
        path = self._path(key)
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if sidecar.exists():
            return sidecar.read_text(encoding="utf-8").strip()
        if not path.exists():
            raise ValidationError_("blob object missing", details={"code": "BLOB_MISSING"})
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def delete(self, key: str) -> None:
        self._validate_key(key)
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path)
            return
        if path.exists():
            path.unlink()
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if sidecar.exists():
            sidecar.unlink()

    def move(self, source_key: str, target_key: str) -> str:
        self._validate_key(source_key)
        self._validate_key(target_key)
        src = self._path(source_key)
        dst = self._path(target_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), str(dst))
        sidecar = src.with_suffix(src.suffix + ".sha256")
        if sidecar.exists():
            sidecar_target = dst.with_suffix(dst.suffix + ".sha256")
            shutil.move(str(sidecar), str(sidecar_target))
        return target_key


def build_storage_key(owner_scope_kind: str, owner_id: UUID, blob_id: UUID, suffix: str = "payload.bin") -> str:
    """Compose an opaque, owner-namespaced storage key.

    The function deliberately hides user-supplied filenames; only the
    canonical blob_id and a sanitised suffix appear in the key.  This is
    the layer that fulfils TF-OPS-003 FR-11 (``storage key 不包含用户
    文件名、邮箱或可猜测项目 ID``).
    """
    safe_kind = _safe_segment(owner_scope_kind)
    return f"{safe_kind}/{owner_id}/{blob_id}/{suffix}"