"""TF-WF-005: PostgreSQL-backed Artifact repository.

Persistent ArtifactVersion CRUD with owner-scope enforcement and
immutability.  The repository never exposes an ``update`` path — once
written, an ArtifactVersion row is immutable.  Stale propagation is
driven by callers comparing upstream ``artifact_id`` versions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import CrossOwnerError, NotFoundError, ValidationError_
from src.infra.db.models import ArtifactVersionModel
from src.infra.db.session import get_session_factory
from src.schemas.models import ArtifactRef, OwnerScope


class SqlArtifactRepository:
    """Persistent artifact storage with cross-owner enforcement."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # CRUD
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
        blob_uri: str | None = None,
        artifact_id: uuid.UUID | None = None,
    ) -> ArtifactVersionModel:
        if not schema_id:
            raise ValidationError_(message="ArtifactVersion 必须声明 schema_id")
        metadata = metadata or {}
        # Inline JSON has no external durability dependency.  Any external
        # content/blob locator, however, must carry a completed durability
        # barrier and immutable hash before it becomes a canonical reference.
        # This is enforced below the HTTP layer so providers/workers cannot
        # publish an explainability-breaking URI by calling the repository.
        external_uri = blob_uri or content_uri
        receipt = metadata.get("durability_receipt")
        receipt_is_valid = (
            isinstance(receipt, dict)
            and isinstance(receipt.get("blob_id"), str)
            and bool(receipt["blob_id"].strip())
            and receipt.get("checksum") == content_hash
            and isinstance(receipt.get("durability_class"), str)
            and bool(receipt["durability_class"].strip())
            and isinstance(receipt.get("checkpoint"), str)
            and bool(receipt["checkpoint"].strip())
            and isinstance(receipt.get("protected_at"), str)
            and bool(receipt["protected_at"].strip())
            and receipt.get("restore_point_eligible") is True
            and receipt.get("verified") is True
        )
        if external_uri and (not content_hash.strip() or not receipt_is_valid):
            raise ValidationError_(
                message="外部 Blob 必须附带已验证的 BlobDurabilityReceipt 和 content_hash",
                details={"code": "BLOB_DURABILITY_BARRIER_REQUIRED"},
            )

        artifact_uuid = artifact_id or uuid.uuid4()
        with self._factory.begin() as session:
            row = ArtifactVersionModel(
                artifact_version_id=uuid.uuid4(),
                artifact_id=artifact_uuid,
                schema_id=schema_id,
                schema_version=schema_version,
                owner_scope=owner_scope.scoped_id,
                content_uri=content_uri,
                content_json=content_json or {},
                content_hash=content_hash,
                created_by_run_id=created_by_run_id,
                lineage_input_refs=lineage_input_refs or [],
                blob_uri=blob_uri if blob_uri is not None else content_uri,
                metadata_json=metadata,
                created_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.flush()
            return row

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
        """Return the direct lineage references for a version.

        Lineage output shape:
            {
                "version_id": str,
                "artifact_id": str,
                "input_refs": [ArtifactRef, ...],
                "typed_refs": [dict, ...],
            }
        """
        with self._factory() as session:
            row = session.get(ArtifactVersionModel, version_id)
            if row is None:
                raise NotFoundError("ArtifactVersion", str(version_id))
            refs: list[ArtifactRef] = []
            typed_refs: list[dict[str, Any]] = []
            for raw in row.lineage_input_refs or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    refs.append(ArtifactRef.model_validate(raw))
                except Exception:
                    # Runtime provenance (for example node_run_attempt_id) is
                    # not an ArtifactRef.  Preserve it as opaque, persisted
                    # metadata rather than attempting any cross-owner lookup.
                    typed_refs.append(dict(raw))
            return {
                "version_id": str(row.artifact_version_id),
                "artifact_id": str(row.artifact_id),
                "schema_id": row.schema_id,
                "schema_version": row.schema_version,
                "input_refs": [r.model_dump(mode="json") for r in refs],
                "typed_refs": typed_refs,
            }

    # ------------------------------------------------------------------
    # Stale propagation
    # ------------------------------------------------------------------

    def stale_downstream(
        self, artifact_id: uuid.UUID, owner_scope: OwnerScope
    ) -> list[uuid.UUID]:
        """Return the version_ids that depend on the given ``artifact_id``.

        Used by callers (workflows, agents) to mark downstream ArtifactRefs
        as stale when an upstream ArtifactVersion is replaced.
        """
        with self._factory() as session:
            downstream = session.scalars(
                select(ArtifactVersionModel.artifact_version_id).where(
                    ArtifactVersionModel.owner_scope == owner_scope.scoped_id,
                    ArtifactVersionModel.lineage_input_refs.contains(
                        [{"artifact_id": str(artifact_id)}]
                    ),
                )
            ).all()
            return list(downstream)
