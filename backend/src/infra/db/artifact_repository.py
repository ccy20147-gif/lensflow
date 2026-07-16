"""TF-WF-005 + TF-OPS-003 Artifact repository.

Persistent ArtifactVersion CRUD with **strict** invariants:

1. ArtifactVersion rows are immutable once written — no update path.
2. lineage writes happen in the SAME transaction as the ArtifactVersion
   so any lineage failure rolls back the canonical row and the index
   projections together (TF-WF-005 §10).
3. Every ArtifactVersion that backs onto a Blob MUST cite an
   ``available`` Blob via an ``ArtifactBlobRefModel`` row.  ``uploading
   / quarantined / deletion_pending / deleted`` Blobs cannot anchor a
   canonical ArtifactVersion.
4. Hash, size, status, and reference integrity are enforced server-side
   before the row is committed.

Cross-owner ArtifactRef resolution is a *read-side* concern and lives in
the route layer; this repository only enforces the durable invariants
the runtime depends on.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import CrossOwnerError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ArtifactBlobRefModel,
    ArtifactVersionModel,
    BlobModel,
    LineageEdgeModel,
    LineageEdgeProjectionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import BlobStatus
from src.schemas.models import ArtifactRef, LineageEdge, OwnerScope


def _canonical_lineage(raw_refs: list[dict[str, Any]] | None, *, created_by_run_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Coerce legacy lineage input into the strict LineageEdge shape.

    Each edge must carry ``source_ref``, ``role``, ``order``, ``producer``
    and ``transformation``.  ``captured_policy_refs`` defaults to ``[]``.
    """
    result: list[dict[str, Any]] = []
    for order, raw in enumerate(raw_refs or []):
        if not isinstance(raw, dict):
            raise ValidationError_("lineage ref 必须是对象")
        source = raw.get("source_ref") if isinstance(raw.get("source_ref"), dict) else {
            key: value for key, value in raw.items()
            if key in {"artifact_id", "artifact_version_id", "node_run_attempt_id", "map_item_id", "tool_invocation_id", "resource_revision_id"}
        }
        if not source:
            raise ValidationError_("lineage ref 必须声明固定 source_ref")
        raw_producer = raw.get("producer")
        producer: dict[str, Any] = dict(raw_producer) if isinstance(raw_producer, dict) else {}
        if created_by_run_id and "run_id" not in producer:
            producer = {**producer, "run_id": str(created_by_run_id)}
        result.append({
            "source_ref": source,
            "role": str(raw.get("role", "input")),
            "order": int(raw.get("order", order)),
            "producer": producer,
            "transformation": raw.get("transformation", {}) or {},
            "captured_policy_refs": list(raw.get("captured_policy_refs", []) or []),
        })
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _derive_content_hash(content_uri: str | None, content_json: dict[str, Any] | None, explicit_hash: str | None = None) -> str:
    if explicit_hash:
        return explicit_hash
    hasher = hashlib.sha256()
    if content_uri:
        hasher.update(content_uri.encode("utf-8"))
    if content_json:
        hasher.update(_canonical_json(content_json).encode("utf-8"))
    return hasher.hexdigest()


class SqlArtifactRepository:
    """Persistent artifact storage with cross-owner enforcement."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_version(
        self,
        *,
        owner_scope: OwnerScope,
        schema_id: str,
        schema_version: int,
        content_uri: str = "",
        content_json: dict[str, Any] | None = None,
        content_hash: str = "",
        created_by_run_id: uuid.UUID | None = None,
        lineage_input_refs: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        blob_id: uuid.UUID | None = None,
        blob_uri: str | None = None,
        artifact_id: uuid.UUID | None = None,
        # ``lineage_persist_failure`` exists ONLY for tests: it forces the
        # canonical lineage flush to raise, which lets the unit tests
        # verify that the ArtifactVersion and reference rows roll back
        # together (TF-WF-005 §10).  Production callers MUST NOT set it.
        lineage_persist_failure: BaseException | None = None,
    ) -> ArtifactVersionModel:
        if not schema_id:
            raise ValidationError_(message="ArtifactVersion 必须声明 schema_id")
        metadata = metadata or {}
        artifact_uuid = artifact_id or uuid.uuid4()

        # Lineage parsing happens BEFORE the transaction starts so a
        # validation error cannot leak a half-written row.
        canonical_lineage = _canonical_lineage(lineage_input_refs, created_by_run_id=created_by_run_id)
        # Explicit LineageEdge construction is the canonical validation
        # path; if any edge is malformed we refuse the whole write.
        for edge in canonical_lineage:
            LineageEdge.model_validate(edge)

        effective_hash = _derive_content_hash(blob_uri or content_uri, content_json, content_hash or None)

        # Legacy durability receipt check (preserved for existing callers
        # that have not yet migrated to BlobModel).  When a BlobModel
        # reference is supplied below we re-validate against its canonical
        # status, but a JSON-only row that nevertheless references an
        # external blob_uri still has to clear the receipt barrier.
        external_uri = blob_uri or content_uri
        receipt = metadata.get("durability_receipt")
        receipt_is_valid = (
            isinstance(receipt, dict)
            and isinstance(receipt.get("blob_id"), str)
            and bool(receipt["blob_id"].strip())
            and receipt.get("checksum") == effective_hash
            and isinstance(receipt.get("durability_class"), str)
            and bool(receipt["durability_class"].strip())
            and isinstance(receipt.get("checkpoint"), str)
            and bool(receipt["checkpoint"].strip())
            and isinstance(receipt.get("protected_at"), str)
            and bool(receipt["protected_at"].strip())
            and receipt.get("restore_point_eligible") is True
            and receipt.get("verified") is True
        )
        if external_uri and blob_id is None and (not effective_hash.strip() or not receipt_is_valid):
            raise ValidationError_(
                message="外部 Blob 必须附带已验证的 BlobDurabilityReceipt 和 content_hash",
                details={"code": "BLOB_DURABILITY_BARRIER_REQUIRED"},
            )

        with self._factory.begin() as session:
            # ------------------------------------------------------------------
            # Blob integrity — only ``available`` Blobs may back a version
            # ------------------------------------------------------------------
            if blob_id is not None:
                blob = session.get(BlobModel, blob_id)
                if blob is None or blob.owner_scope != owner_scope.scoped_id:
                    raise CrossOwnerError()
                if blob.status != BlobStatus.AVAILABLE:
                    raise ValidationError_(
                        "ArtifactVersion 必须引用 available 状态的 Blob",
                        details={
                            "code": "BLOB_NOT_AVAILABLE",
                            "blob_id": str(blob_id),
                            "blob_status": blob.status,
                        },
                    )
                if blob.content_hash != effective_hash:
                    raise ValidationError_(
                        "ArtifactVersion 的 content_hash 与 backing Blob 不一致",
                        details={"code": "BLOB_HASH_MISMATCH"},
                    )
            else:
                blob = None

            row = ArtifactVersionModel(
                artifact_version_id=uuid.uuid4(),
                artifact_id=artifact_uuid,
                schema_id=schema_id,
                schema_version=schema_version,
                owner_scope=owner_scope.scoped_id,
                content_uri=content_uri,
                content_json=content_json or {},
                content_hash=effective_hash,
                created_by_run_id=created_by_run_id,
                lineage_input_refs=canonical_lineage,
                blob_uri=blob.storage_key if blob is not None else (blob_uri or content_uri),
                metadata_json=metadata,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()

            # ------------------------------------------------------------------
            # Lineage rows — written in the SAME transaction.  Any failure
            # below propagates and SQLAlchemy rolls back both the artifact
            # row and the references atomically (TF-WF-005 §10).
            # ------------------------------------------------------------------
            if lineage_persist_failure is not None:
                raise lineage_persist_failure

            for order_index, edge in enumerate(canonical_lineage):
                session.add(LineageEdgeModel(
                    edge_id=uuid.uuid4(),
                    artifact_version_id=row.artifact_version_id,
                    order_index=order_index,
                    source_ref=edge["source_ref"],
                    role=edge["role"],
                    producer=edge["producer"],
                    transformation=edge["transformation"],
                    captured_policy_refs=edge.get("captured_policy_refs", []),
                    created_at=datetime.now(timezone.utc),
                ))
            if blob is not None:
                session.add(ArtifactBlobRefModel(
                    ref_id=uuid.uuid4(),
                    artifact_version_id=row.artifact_version_id,
                    blob_id=blob.blob_id,
                    owner_scope=owner_scope.scoped_id,
                    role="primary",
                    created_at=datetime.now(timezone.utc),
                ))
            session.flush()
            return row

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_version(self, version_id: uuid.UUID, owner_scope: OwnerScope) -> ArtifactVersionModel:
        """Load a single version, enforcing owner_scope."""
        with self._factory() as session:
            row = session.scalar(
                select(ArtifactVersionModel).where(
                    ArtifactVersionModel.artifact_version_id == version_id,
                    ArtifactVersionModel.owner_scope == owner_scope.scoped_id,
                )
            )
            if row is None:
                exists = session.get(ArtifactVersionModel, version_id)
                if exists is not None and exists.owner_scope != owner_scope.scoped_id:
                    raise CrossOwnerError()
                raise NotFoundError("ArtifactVersion", str(version_id))
            return row

    def list_versions(
        self,
        *,
        owner_scope: OwnerScope,
        schema_id: str | None = None,
        artifact_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ArtifactVersionModel]:
        stmt = select(ArtifactVersionModel).where(
            ArtifactVersionModel.owner_scope == owner_scope.scoped_id
        )
        if schema_id is not None:
            stmt = stmt.where(ArtifactVersionModel.schema_id == schema_id)
        if artifact_id is not None:
            stmt = stmt.where(ArtifactVersionModel.artifact_id == artifact_id)
        stmt = stmt.order_by(ArtifactVersionModel.created_at.desc()).offset(offset).limit(limit)
        with self._factory() as session:
            return list(session.scalars(stmt))

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    def lineage(self, version_id: uuid.UUID) -> dict[str, Any]:
        """Return both the durable lineage graph and the denormalised view.

        The durable rows (LineageEdgeModel) are the canonical answer; the
        ``lineage_input_refs`` JSON column on ArtifactVersionModel is
        kept only as a denormalised convenience snapshot.  AC-6 requires
        both to be rebuilt deterministically from canonical data.
        """
        with self._factory() as session:
            row = session.get(ArtifactVersionModel, version_id)
            if row is None:
                raise NotFoundError("ArtifactVersion", str(version_id))
            edges = session.scalars(
                select(LineageEdgeModel)
                .where(LineageEdgeModel.artifact_version_id == version_id)
                .order_by(LineageEdgeModel.order_index)
            )
            edge_payload = [
                {
                    "edge_id": str(edge.edge_id),
                    "order_index": edge.order_index,
                    "role": edge.role,
                    "source_ref": edge.source_ref,
                    "producer": edge.producer,
                    "transformation": edge.transformation,
                    "captured_policy_refs": edge.captured_policy_refs or [],
                }
                for edge in edges
            ]
            refs: list[ArtifactRef] = []
            typed_refs: list[dict[str, Any]] = []
            for raw in row.lineage_input_refs or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    refs.append(ArtifactRef.model_validate(raw))
                except Exception:
                    typed_refs.append(dict(raw))
            return {
                "version_id": str(row.artifact_version_id),
                "artifact_id": str(row.artifact_id),
                "schema_id": row.schema_id,
                "schema_version": row.schema_version,
                "durable_edges": edge_payload,
                "input_refs": [r.model_dump(mode="json") for r in refs],
                "typed_refs": typed_refs,
            }

    def lineage_edges_for(self, version_id: uuid.UUID) -> list[LineageEdge]:
        with self._factory() as session:
            edges = list(session.scalars(
                select(LineageEdgeModel)
                .where(LineageEdgeModel.artifact_version_id == version_id)
                .order_by(LineageEdgeModel.order_index)
            ))
            return [
                LineageEdge(
                    source_ref=edge.source_ref,
                    role=edge.role,
                    order=edge.order_index,
                    producer=edge.producer,
                    transformation=edge.transformation,
                    captured_policy_refs=list(edge.captured_policy_refs or []),
                )
                for edge in edges
            ]

    # ------------------------------------------------------------------
    # Stale propagation
    # ------------------------------------------------------------------

    def stale_downstream(
        self, artifact_id: uuid.UUID, owner_scope: OwnerScope
    ) -> list[uuid.UUID]:
        with self._factory() as session:
            downstream = list(session.scalars(
                select(ArtifactVersionModel.artifact_version_id).where(
                    ArtifactVersionModel.owner_scope == owner_scope.scoped_id,
                    ArtifactVersionModel.lineage_input_refs.contains(
                        [{"artifact_id": str(artifact_id)}]
                    ),
                )
            ))
            return list(downstream)

    def rebuild_lineage_projection(self, owner_scope: OwnerScope) -> int:
        """Re-derive the ``lineage_edges_projections`` table from canonical
        ``lineage_edges`` rows.

        This rebuild MUST NOT touch any canonical table — neither the
        ArtifactVersion row nor its ``lineage_input_refs`` snapshot.
        The projection lives in ``lineage_edges_projections`` so a
        wipe-and-rebuild never mutates immutable history.

        Returns the number of projection rows written.
        """
        rewritten = 0
        with self._factory.begin() as session:
            # Drop only the projection rows for this owner.
            owner_artifacts = list(session.scalars(
                select(ArtifactVersionModel.artifact_version_id).where(
                    ArtifactVersionModel.owner_scope == owner_scope.scoped_id
                )
            ))
            if owner_artifacts:
                session.query(LineageEdgeProjectionModel).filter(
                    LineageEdgeProjectionModel.artifact_version_id.in_(owner_artifacts)
                ).delete(synchronize_session=False)
            rows = list(session.scalars(
                select(ArtifactVersionModel).where(
                    ArtifactVersionModel.owner_scope == owner_scope.scoped_id
                )
            ))
            for row in rows:
                edges = list(session.scalars(
                    select(LineageEdgeModel)
                    .where(LineageEdgeModel.artifact_version_id == row.artifact_version_id)
                    .order_by(LineageEdgeModel.order_index)
                ))
                for edge in edges:
                    session.add(LineageEdgeProjectionModel(
                        projection_id=uuid.uuid4(),
                        artifact_version_id=edge.artifact_version_id,  # type: ignore[arg-type]
                        order_index=edge.order_index,  # type: ignore[arg-type]
                        source_ref=edge.source_ref,
                        role=edge.role,
                        producer=edge.producer,
                        transformation=edge.transformation,
                        captured_policy_refs=list(edge.captured_policy_refs or []),
                        rebuilt_at=datetime.now(timezone.utc),
                    ))
                    rewritten += 1
            session.flush()
        return rewritten

    def lineage_projection_rows(self, owner_scope: OwnerScope) -> list[dict[str, Any]]:
        """Read-only accessor for the rebuilt projection."""
        with self._factory() as session:
            rows = list(session.scalars(
                select(LineageEdgeProjectionModel)
                .where(LineageEdgeProjectionModel.artifact_version_id.in_(
                    select(ArtifactVersionModel.artifact_version_id).where(
                        ArtifactVersionModel.owner_scope == owner_scope.scoped_id
                    )
                ))
                .order_by(
                    LineageEdgeProjectionModel.artifact_version_id,
                    LineageEdgeProjectionModel.order_index,
                )
            ))
            return [
                {
                    "artifact_version_id": str(row.artifact_version_id),  # type: ignore[arg-type]
                    "order_index": row.order_index,  # type: ignore[arg-type]
                    "role": row.role,
                    "source_ref": row.source_ref,
                    "producer": row.producer,
                    "transformation": row.transformation,
                    "captured_policy_refs": list(row.captured_policy_refs or []),
                }
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Reference safety
    # ------------------------------------------------------------------

    def blob_references(self, blob_id: UUID, owner_scope: OwnerScope) -> list[UUID]:
        """List ArtifactVersion ids that cite ``blob_id`` within owner_scope."""
        with self._factory() as session:
            return list(session.scalars(
                select(ArtifactBlobRefModel.artifact_version_id).where(
                    ArtifactBlobRefModel.blob_id == blob_id,
                    ArtifactBlobRefModel.owner_scope == owner_scope.scoped_id,
                )
            ))

    def assert_blob_mutation_allowed(self, blob_id: UUID, owner_scope: OwnerScope) -> None:
        """Server-side guard for destructive blob lifecycle operations.

        Performs the same durable scan as ``SqlBlobRepository.assert_lifecycle_allowed``,
        but anchored on the ArtifactVersion -> ArtifactBlobRefModel join
        instead of the old ``blob_uri`` text match.
        """
        with self._factory() as session:
            blob = session.get(BlobModel, blob_id)
            if blob is None:
                raise NotFoundError("Blob", str(blob_id))
            if blob.owner_scope != owner_scope.scoped_id:
                raise CrossOwnerError()
            refs = session.scalars(
                select(ArtifactBlobRefModel.artifact_version_id).where(
                    ArtifactBlobRefModel.blob_id == blob_id,
                    ArtifactBlobRefModel.owner_scope == owner_scope.scoped_id,
                ).limit(1)
            ).first()
            if refs is not None:
                raise ValidationError_(
                    "Blob 仍被有效 ArtifactVersion 引用，禁止破坏性操作",
                    details={"artifact_version_id": str(refs)},
                )

    def assert_blob_mutation_allowed_for_uri(self, blob_uri: str, owner_scope: OwnerScope) -> None:
        """Legacy text-match guard preserved for existing callers.

        New code MUST migrate to ``assert_blob_mutation_allowed`` keyed
        on ``blob_id``; this helper exists only so the HTTP
        ``/blobs/delete-check`` route continues to honour pre-existing
        text callers during the migration window.
        """
        with self._factory() as session:
            referenced = session.scalar(select(ArtifactVersionModel.artifact_version_id).where(
                ArtifactVersionModel.owner_scope == owner_scope.scoped_id,
                ArtifactVersionModel.blob_uri == blob_uri,
            ).limit(1))
            if referenced is not None:
                raise ValidationError_("Blob 仍被有效 ArtifactVersion 引用，禁止破坏性操作", details={"artifact_version_id": str(referenced)})