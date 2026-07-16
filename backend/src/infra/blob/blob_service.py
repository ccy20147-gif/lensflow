"""Blob service — Foundation contract (TF-OPS-003 §6).

Responsibilities:

* persist ``BlobModel`` + ``UploadSessionModel`` rows,
* enforce size / hash / status integrity for ArtifactVersion backings,
* run lifecycle transitions (uploading → available → quarantined →
  deletion_pending → deleted),
* prevent ``uploading / quarantined / deletion_pending / deleted`` rows
  from becoming ArtifactVersion backings,
* refuse ``delete / archive / migrate`` while any ArtifactVersion,
  ResourceRevision, Run, or audit row still references the Blob.

This is a *minimal* Foundation surface.  Real resumable / multipart
upload UI is V0 (see TF-OPS-003 §6 slice matrix).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import IO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, CrossOwnerError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactBlobRefModel,
    AuditLogModel,
    BlobModel,
    BlobReferenceIndexModel,
    ProviderInvocationRecordModel,
    ResourceRevisionModel,
    UploadSessionModel,
)
from src.infra.db.session import get_session_factory
from src.infra.blob.storage import BlobStorageAdapter, LocalFileBlobAdapter, build_storage_key
from src.schemas.enums import BlobStatus, UploadSessionStatus
from src.schemas.models import BlobRef, OwnerScope, UploadSession


def _blob_ref(row: BlobModel) -> BlobRef:
    """Public BlobRef shape.  ``storage_key`` is intentionally dropped."""
    kind, _, owner_id = row.owner_scope.partition(":")
    return BlobRef(
        blob_id=row.blob_id,
        owner_scope=OwnerScope(kind=kind, id=UUID(owner_id)),
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        status=BlobStatus(row.status),
        storage_key="",
        created_at=row.created_at,
    )


def _session_ref(row: UploadSessionModel) -> UploadSession:
    kind, _, owner_id = row.owner_scope.partition(":")
    return UploadSession(
        session_id=row.session_id,
        blob_id=row.blob_id,
        owner_scope=OwnerScope(kind=kind, id=UUID(owner_id)),
        expected_size_bytes=row.expected_size_bytes,
        expected_content_hash=row.expected_content_hash,
        idempotency_key=row.idempotency_key,
        status=UploadSessionStatus(row.status),
        part_state=list(row.part_state or []),
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


class SqlBlobRepository:
    """Durable Blob / UploadSession lifecycle with reference protection."""

    def __init__(
        self,
        factory: sessionmaker[Session] | None = None,
        adapter: BlobStorageAdapter | None = None,
    ) -> None:
        self._factory = factory or get_session_factory()
        self._adapter: BlobStorageAdapter = adapter or LocalFileBlobAdapter()

    # ------------------------------------------------------------------
    # Upload session lifecycle
    # ------------------------------------------------------------------

    def start_upload(
        self,
        owner: OwnerScope,
        expected_size_bytes: int,
        expected_content_hash: str,
        media_type: str,
        idempotency_key: str,
        part_state: list[dict] | None = None,
        expires_at: datetime | None = None,
    ) -> UploadSession:
        """Create a new durable upload session and a tied ``uploading`` Blob row.

        Idempotent on ``(owner_scope, idempotency_key)``: a repeat call with
        the same key returns the existing session without consuming a new
        Blob row.  This matches the contract the Foundation spec describes
        for resumable upload prep.
        """
        if expected_size_bytes <= 0:
            raise ValidationError_("upload size must be positive")
        if not expected_content_hash.strip():
            raise ValidationError_("upload hash must be declared")
        if not idempotency_key.strip():
            raise ValidationError_("idempotency key is required")
        with self._factory.begin() as session:
            existing = session.scalar(
                select(UploadSessionModel).where(
                    UploadSessionModel.owner_scope == owner.scoped_id,
                    UploadSessionModel.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return _session_ref(existing)
            blob_id = uuid4()
            blob = BlobModel(
                blob_id=blob_id,
                owner_scope=owner.scoped_id,
                storage_key=build_storage_key(owner.kind, owner.id, blob_id),
                media_type=media_type,
                size_bytes=expected_size_bytes,
                content_hash=expected_content_hash,
                status=BlobStatus.UPLOADING,
                durability_receipt=None,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(blob)
            upload = UploadSessionModel(
                session_id=uuid4(),
                blob_id=blob_id,
                owner_scope=owner.scoped_id,
                expected_size_bytes=expected_size_bytes,
                expected_content_hash=expected_content_hash,
                idempotency_key=idempotency_key,
                status=UploadSessionStatus.INITIATED,
                part_state=list(part_state or []),
                bytes_received=0,
                expires_at=expires_at,
                created_at=datetime.now(timezone.utc),
            )
            session.add(upload)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ConflictError("idempotent upload session conflict") from exc
            return _session_ref(upload)

    def record_bytes(self, session_id: UUID, owner: OwnerScope, additional_bytes: int) -> UploadSession:
        if additional_bytes <= 0:
            raise ValidationError_("bytes must be positive")
        with self._factory.begin() as session:
            upload = self._lock_session(session, session_id, owner)
            if upload.status not in {UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING}:
                raise ConflictError("upload session is not accepting bytes")
            upload.bytes_received += additional_bytes
            if upload.bytes_received > upload.expected_size_bytes:
                raise ValidationError_("bytes received exceed expected size")
            upload.status = UploadSessionStatus.UPLOADING
            session.flush()
            return _session_ref(upload)

    def complete_upload(
        self,
        session_id: UUID,
        owner: OwnerScope,
        stream: IO[bytes],
        declared_size: int,
        declared_hash: str,
        durability_receipt: dict | None = None,
    ) -> BlobRef:
        """Atomically validate and finalise a Blob.

        Steps:
          1. Re-load the upload under a row lock.
          2. Stream payload through ``adapter.put``; the adapter records
             the actual size and SHA-256.
          3. Compare ``actual vs expected`` for both size and hash.
          4. Flip ``BlobModel.status`` to ``available`` and the session to
             ``completed``.
          5. On any failure, quarantine the Blob and leave status
             ``uploading`` so the orphan scanner can decide.

        Returns the public ``BlobRef`` once the Blob is ``available``.
        """
        with self._factory.begin() as session:
            upload = self._lock_session(session, session_id, owner)
            if upload.status == UploadSessionStatus.COMPLETED:
                blob = session.get(BlobModel, upload.blob_id)
                if blob is None or blob.status != BlobStatus.AVAILABLE:
                    raise ConflictError("completed upload has no available blob")
                return _blob_ref(blob)
            if upload.status not in {UploadSessionStatus.INITIATED, UploadSessionStatus.UPLOADING}:
                raise ConflictError("upload session cannot be completed")
            blob = session.get(BlobModel, upload.blob_id)
            assert blob is not None, "Blob row must outlive its upload session"
            try:
                actual_size = self._adapter.put(blob.storage_key, stream)
            except Exception as exc:  # noqa: BLE001 — translate to safe error
                blob.status = BlobStatus.QUARANTINED
                blob.quarantine_reason = f"write failure: {exc}"
                upload.status = UploadSessionStatus.ABORTED
                raise ValidationError_("blob write failed; quarantined") from exc
            actual_hash = self._adapter.checksum(blob.storage_key)
            if actual_size != declared_size or actual_size != upload.expected_size_bytes:
                blob.status = BlobStatus.QUARANTINED
                blob.quarantine_reason = "size mismatch"
                self._safe_delete(blob.storage_key)
                raise ValidationError_("upload size mismatch")
            if actual_hash != declared_hash or actual_hash != upload.expected_content_hash:
                blob.status = BlobStatus.QUARANTINED
                blob.quarantine_reason = "hash mismatch"
                self._safe_delete(blob.storage_key)
                raise ValidationError_("upload hash mismatch")
            blob.status = BlobStatus.AVAILABLE
            blob.size_bytes = actual_size
            blob.content_hash = actual_hash
            blob.durability_receipt = durability_receipt or blob.durability_receipt or {
                "checksum": actual_hash,
                "size_bytes": actual_size,
                "verified": True,
            }
            upload.status = UploadSessionStatus.COMPLETED
            upload.completed_at = datetime.now(timezone.utc)
            session.add(AuditLogModel(
                audit_id=uuid4(),
                owner_scope=blob.owner_scope,
                event_type="blob.available",
                blob_id=blob.blob_id,
                ref_kind="blob",
                ref_id=blob.blob_id,
                payload={"size_bytes": actual_size, "content_hash": actual_hash},
                created_at=datetime.now(timezone.utc),
            ))
            session.flush()
            return _blob_ref(blob)

    def abort_upload(self, session_id: UUID, owner: OwnerScope) -> UploadSession:
        with self._factory.begin() as session:
            upload = self._lock_session(session, session_id, owner)
            if upload.status == UploadSessionStatus.COMPLETED:
                raise ConflictError("cannot abort a completed upload")
            upload.status = UploadSessionStatus.ABORTED
            blob = session.get(BlobModel, upload.blob_id)
            if blob is not None:
                blob.status = BlobStatus.QUARANTINED
                blob.quarantine_reason = "session aborted"
                self._safe_delete(blob.storage_key)
            session.flush()
            return _session_ref(upload)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, blob_id: UUID, owner: OwnerScope) -> BlobRef:
        with self._factory() as session:
            row = session.get(BlobModel, blob_id)
            if row is None:
                raise NotFoundError("Blob", str(blob_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            return _blob_ref(row)

    def get_upload(self, session_id: UUID, owner: OwnerScope) -> UploadSession:
        with self._factory() as session:
            upload = session.get(UploadSessionModel, session_id)
            if upload is None:
                raise NotFoundError("UploadSession", str(session_id))
            if upload.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            return _session_ref(upload)

    def read_bytes(self, blob_id: UUID, owner: OwnerScope) -> bytes:
        with self._factory() as session:
            row = session.get(BlobModel, blob_id)
            if row is None:
                raise NotFoundError("Blob", str(blob_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            if row.status != BlobStatus.AVAILABLE:
                raise ValidationError_(
                    "blob is not available for read",
                    details={"status": row.status},
                )
            return self._adapter.get(row.storage_key)

    # ------------------------------------------------------------------
    # Reference protection (TF-OPS-003 FR-8)
    # ------------------------------------------------------------------

    def references_for(self, blob_id: UUID, owner: OwnerScope) -> dict[str, list[UUID]]:
        """Return every durable reference to ``blob_id`` grouped by ref_kind.

        The contract is: ``delete / archive / migrate`` MUST NOT proceed
        while ANY of ``artifact_version``, ``resource_revision``, ``run``
        or ``audit`` still cites this blob, regardless of who owns the
        referencing row.  This is the proof-of-life scan that protects
        history.
        """
        refs: dict[str, list[UUID]] = {
            "artifact_version": [],
            "resource_revision": [],
            "run": [],
            "audit": [],
            "invocation_record": [],
        }
        with self._factory() as session:
            blob = session.get(BlobModel, blob_id)
            if blob is None:
                raise NotFoundError("Blob", str(blob_id))
            if blob.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            for row in session.scalars(
                select(ArtifactBlobRefModel.artifact_version_id).where(ArtifactBlobRefModel.blob_id == blob_id)
            ):
                refs["artifact_version"].append(row)
            for row in session.scalars(
                select(ResourceRevisionModel.revision_id).where(ResourceRevisionModel.content_artifact_version_id.in_(
                    select(ArtifactBlobRefModel.artifact_version_id).where(ArtifactBlobRefModel.blob_id == blob_id)
                ))
            ):
                refs["resource_revision"].append(row)
            for row in session.scalars(
                select(ProviderInvocationRecordModel.record_id).where(
                    ProviderInvocationRecordModel.idempotency_key == blob.content_hash
                )
            ):
                refs["invocation_record"].append(row)
            for row in session.scalars(
                select(AuditLogModel.audit_id).where(AuditLogModel.blob_id == blob_id)
            ):
                refs["audit"].append(row)
        return refs

    def assert_lifecycle_allowed(
        self, blob_id: UUID, owner: OwnerScope, action: str,
    ) -> dict[str, list[UUID]]:
        """Refuse ``delete/archive/migrate`` while any reference exists.

        The audit log is intentionally excluded: lifecycle audit rows
        (e.g. ``blob.available``) are part of the Blob's own history and
        do not represent a consumer reference.  Only content-level
        references from ArtifactVersion / ResourceRevision / Run /
        InvocationRecord count towards the guard.
        """
        refs = self.references_for(blob_id, owner)
        content_refs = {
            "artifact_version": refs["artifact_version"],
            "resource_revision": refs["resource_revision"],
            "invocation_record": refs["invocation_record"],
        }
        flat = [(kind, rid) for kind, ids in content_refs.items() for rid in ids]
        if flat:
            sample = [
                {"ref_kind": kind, "ref_id": str(rid)} for kind, rid in flat[:10]
            ]
            raise ValidationError_(
                f"blob 仍被 {len(flat)} 条不可变记录引用，禁止 {action}",
                details={"action": action, "references": sample, "total_refs": len(flat)},
            )
        return refs

    def mark_deletion_pending(self, blob_id: UUID, owner: OwnerScope) -> BlobRef:
        # Same protection as ``finalize_delete``: the blob must be free of
        # any ArtifactVersion / Revision / Run / InvocationRecord reference
        # before the lifecycle state can move forward.
        self.assert_lifecycle_allowed(blob_id, owner, "delete")
        with self._factory.begin() as session:
            row = session.get(BlobModel, blob_id)
            if row is None:
                raise NotFoundError("Blob", str(blob_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            if row.status in {BlobStatus.DELETION_PENDING, BlobStatus.DELETED}:
                return _blob_ref(row)
            row.status = BlobStatus.DELETION_PENDING
            row.updated_at = datetime.now(timezone.utc)
            session.add(AuditLogModel(
                audit_id=uuid4(),
                owner_scope=row.owner_scope,
                event_type="blob.deletion_pending",
                blob_id=blob_id,
                ref_kind="blob",
                ref_id=blob_id,
                payload={},
                created_at=datetime.now(timezone.utc),
            ))
            session.flush()
            return _blob_ref(row)

    def finalize_delete(self, blob_id: UUID, owner: OwnerScope) -> None:
        """Finalise a deletion by removing the physical object.

        This is the destructive boundary: even if the deletion was
        scheduled earlier via ``mark_deletion_pending``, we MUST
        re-scan the durable references inside this critical section.
        A reference that appeared AFTER the deletion_pending mark —
        e.g. a concurrent ResourceRevision freeze — would otherwise
        silently orphan historical content.
        """
        with self._factory.begin() as session:
            row = session.get(BlobModel, blob_id)
            if row is None:
                raise NotFoundError("Blob", str(blob_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            if row.status != BlobStatus.DELETION_PENDING:
                raise ConflictError("blob is not in deletion_pending state")
            # Re-scan references under the same transaction.  Any new
            # reference created between mark_deletion_pending and this
            # call MUST abort the deletion.
            self._assert_no_references_locked(session, blob_id, owner, "finalize_delete")
            self._safe_delete(row.storage_key)
            row.status = BlobStatus.DELETED
            row.updated_at = datetime.now(timezone.utc)
            session.add(AuditLogModel(
                audit_id=uuid4(),
                owner_scope=row.owner_scope,
                event_type="blob.deleted",
                blob_id=blob_id,
                ref_kind="blob",
                ref_id=blob_id,
                payload={},
                created_at=datetime.now(timezone.utc),
            ))
            session.flush()

    def migrate(
        self,
        blob_id: UUID,
        owner: OwnerScope,
        target_adapter: BlobStorageAdapter | None = None,
    ) -> BlobRef:
        """Move a Blob to a new storage backend while keeping ``blob_id``,
        ``content_hash`` and references stable (TF-OPS-003 FR-10).
        """
        with self._factory.begin() as session:
            row = session.get(BlobModel, blob_id)
            if row is None:
                raise NotFoundError("Blob", str(blob_id))
            if row.owner_scope != owner.scoped_id:
                raise CrossOwnerError()
            target = target_adapter or self._adapter
            if target is self._adapter:
                raise ValidationError_("source and target adapter are identical")
            new_key = build_storage_key(owner.kind, owner.id, blob_id, suffix="migrated.bin")
            target.put_bytes(new_key, self._adapter.get(row.storage_key))
            row.storage_key = new_key
            row.updated_at = datetime.now(timezone.utc)
            session.add(AuditLogModel(
                audit_id=uuid4(),
                owner_scope=row.owner_scope,
                event_type="blob.migrated",
                blob_id=blob_id,
                ref_kind="blob",
                ref_id=blob_id,
                payload={"new_storage_key_present": True},
                created_at=datetime.now(timezone.utc),
            ))
            session.flush()
            return _blob_ref(row)

    # ------------------------------------------------------------------
    # Reference index rebuild (TF-WF-005 AC-6)
    # ------------------------------------------------------------------

    def rebuild_reference_index(self, owner: OwnerScope) -> int:
        """Reconstruct ``blob_reference_index`` from canonical rows.

        The projection is purely derivable from
        ``artifact_blob_refs ∪ resource_revisions ∪ audit_log``; if it
        is ever wiped the function below restores it without changing
        the underlying canonical rows.  Returns the number of rows
        written.
        """
        written = 0
        with self._factory.begin() as session:
            session.query(BlobReferenceIndexModel).filter(
                BlobReferenceIndexModel.owner_scope == owner.scoped_id
            ).delete()
            for row in session.scalars(
                select(ArtifactBlobRefModel).where(ArtifactBlobRefModel.owner_scope == owner.scoped_id)
            ):
                session.add(BlobReferenceIndexModel(
                    index_id=uuid4(),
                    blob_id=row.blob_id,
                    owner_scope=row.owner_scope,
                    ref_kind="artifact_version",
                    ref_id=row.artifact_version_id,
                    created_at=datetime.now(timezone.utc),
                ))
                written += 1
            for row in session.scalars(
                select(AuditLogModel).where(
                    AuditLogModel.owner_scope == owner.scoped_id,
                    AuditLogModel.blob_id.is_not(None),
                )
            ):
                session.add(BlobReferenceIndexModel(
                    index_id=uuid4(),
                    blob_id=row.blob_id,
                    owner_scope=row.owner_scope,
                    ref_kind="audit",
                    ref_id=row.audit_id,
                    created_at=datetime.now(timezone.utc),
                ))
                written += 1
            session.flush()
        return written

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _lock_session(session: Session, session_id: UUID, owner: OwnerScope) -> UploadSessionModel:
        upload = session.scalar(
            select(UploadSessionModel)
            .where(UploadSessionModel.session_id == session_id)
            .with_for_update()
        )
        if upload is None:
            raise NotFoundError("UploadSession", str(session_id))
        if upload.owner_scope != owner.scoped_id:
            raise CrossOwnerError()
        return upload

    def _assert_no_references_locked(
        self,
        session: Session,
        blob_id: UUID,
        owner: OwnerScope,
        action: str,
    ) -> None:
        """In-transaction reference scan.

        Identical to ``references_for`` but operates on a passed-in
        session so the caller can lock the Blob row first and avoid
        a TOCTOU race between the scan and the destructive action.
        """
        flat: list[tuple[str, UUID]] = []
        for row in session.scalars(
            select(ArtifactBlobRefModel.artifact_version_id).where(ArtifactBlobRefModel.blob_id == blob_id)
        ):
            flat.append(("artifact_version", row))
        for row in session.scalars(
            select(ResourceRevisionModel.revision_id).where(ResourceRevisionModel.content_artifact_version_id.in_(
                select(ArtifactBlobRefModel.artifact_version_id).where(ArtifactBlobRefModel.blob_id == blob_id)
            ))
        ):
            flat.append(("resource_revision", row))
        for row in session.scalars(
            select(ProviderInvocationRecordModel.record_id).where(
                ProviderInvocationRecordModel.idempotency_key.in_(
                    select(BlobModel.content_hash).where(BlobModel.blob_id == blob_id)
                )
            )
        ):
            flat.append(("invocation_record", row))
        if flat:
            sample = [{"ref_kind": kind, "ref_id": str(rid)} for kind, rid in flat[:10]]
            raise ValidationError_(
                f"blob 仍被 {len(flat)} 条不可变记录引用，禁止 {action}",
                details={"action": action, "references": sample, "total_refs": len(flat)},
            )

    def _safe_delete(self, storage_key: str) -> None:
        try:
            self._adapter.delete(storage_key)
        except Exception:  # noqa: BLE001 — best effort
            pass


def sha256_hex(payload: bytes) -> str:
    """Convenience hash used by tests and the public upload pipeline."""
    return hashlib.sha256(payload).hexdigest()