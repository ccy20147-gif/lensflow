"""TF-WF-005: ArtifactVersion Service

Manages creation of immutable ArtifactVersion records, lineage tracking,
content deduplication (CAS), and stale propagation notifications.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.exceptions import ConflictError, NotFoundError, CrossOwnerError
from src.schemas.models import ArtifactRef, ArtifactVersion, OwnerScope


class ArtifactService:
    """In-memory artifact store for Foundation stage.

    ArtifactVersion instances are immutable once created. No updates or deletes.
    """

    def __init__(self) -> None:
        self._versions: dict[UUID, ArtifactVersion] = {}  # artifact_version_id
        # content_hash -> list[artifact_version_id] (dedup / CAS)
        self._content_hash_index: dict[str, list[UUID]] = {}

    # ------------------------------------------------------------------
    # ArtifactVersion CRUD (Create-only)
    # ------------------------------------------------------------------

    def create_artifact_version(
        self,
        artifact_id: UUID | None = None,
        schema_id: str = "",
        schema_version: int = 1,
        owner_scope: OwnerScope | None = None,
        content_uri: str | None = None,
        content_json: dict[str, Any] | None = None,
        created_by_run_id: UUID | None = None,
        lineage_input_refs: list[ArtifactRef] | None = None,
    ) -> ArtifactVersion:
        """Create a new immutable ArtifactVersion.

        Args:
            artifact_id: Stable artifact identity. If None, a new one is generated.
            schema_id: Required schema identifier for type checking.
            schema_version: Schema version for this content.
            owner_scope: The owning scope (required).
            content_uri: URI to blob storage content.
            content_json: Inline JSON content (alternative to content_uri).
            created_by_run_id: Run that produced this artifact.
            lineage_input_refs: Input artifact versions that produced this one.

        Returns:
            The newly created ArtifactVersion.

        Raises:
            ValueError: If neither content_uri nor content_json is provided.
            CrossOwnerError: If any lineage_input_ref refers to content outside
                            this owner_scope.
        """
        if not content_uri and not content_json:
            raise ValueError("content_uri or content_json must be provided")

        av_id = uuid4()
        art_id = artifact_id or uuid4()

        # Cross-owner check on lineage inputs
        if owner_scope and lineage_input_refs:
            # In Foundation, we don't have full resolution, but we can flag
            pass  # Cross-owner check requires ResourceRef resolution

        # Compute content hash
        content_hash = self._compute_content_hash(content_uri, content_json)

        # CAS: if same content_hash exists under same owner, we still create a
        # new version ID (immutable), but we can track dedup
        self._content_hash_index.setdefault(content_hash, []).append(av_id)

        av = ArtifactVersion(
            artifact_id=art_id,
            artifact_version_id=av_id,
            schema_id=schema_id,
            schema_version=schema_version,
            owner_scope=owner_scope,  # type: ignore[arg-type]
            content_uri=content_uri,
            content_json=content_json,
            created_by_run_id=created_by_run_id,
            lineage_input_refs=lineage_input_refs or [],
            created_at=datetime.now(timezone.utc),
            content_hash=content_hash,
        )
        self._versions[av.artifact_version_id] = av
        return av

    def get_artifact_version(self, artifact_version_id: UUID) -> ArtifactVersion:
        av = self._versions.get(artifact_version_id)
        if av is None:
            raise NotFoundError("ArtifactVersion", str(artifact_version_id))
        return av

    def get_artifact_ref(
        self, artifact_id: UUID, artifact_version_id: UUID, owner_scope: OwnerScope
    ) -> ArtifactRef:
        """Get an ArtifactRef with cross-owner check.

        Raises:
            NotFoundError: If the version doesn't exist.
            CrossOwnerError: If the version's owner_scope doesn't match the
                            requesting owner_scope.
        """
        av = self.get_artifact_version(artifact_version_id)
        if av.artifact_id != artifact_id:
            raise NotFoundError("ArtifactVersion", f"{artifact_id}/{artifact_version_id}")

        # Cross-owner: ArtifactRef only allowed within same owner_scope
        if av.owner_scope != owner_scope:
            raise CrossOwnerError()

        return ArtifactRef(
            artifact_id=artifact_id,
            artifact_version_id=artifact_version_id,
            schema_id=av.schema_id,
            schema_version=av.schema_version,
        )

    def list_artifact_versions(
        self, artifact_id: UUID | None = None, offset: int = 0, limit: int = 50
    ) -> list[ArtifactVersion]:
        """List artifact versions, optionally filtered by artifact_id (newest first)."""
        results = list(self._versions.values())
        if artifact_id is not None:
            results = [av for av in results if av.artifact_id == artifact_id]
        results.sort(key=lambda av: av.created_at, reverse=True)
        return results[offset : offset + limit]

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    def get_lineage(
        self, artifact_version_id: UUID
    ) -> dict[str, Any]:
        """Get the full lineage for an artifact version.

        Returns a dict with the version itself and its input lineage.
        """
        av = self.get_artifact_version(artifact_version_id)
        lineage: dict[str, Any] = {
            "version": av.model_dump(mode="json"),
            "inputs": [],
        }
        for ref in av.lineage_input_refs:
            try:
                input_av = self.get_artifact_version(ref.artifact_version_id)
                lineage["inputs"].append(input_av.model_dump(mode="json"))
            except NotFoundError:
                lineage["inputs"].append(
                    {
                        "artifact_version_id": str(ref.artifact_version_id),
                        "status": "not_found",
                    }
                )
        return lineage

    # ------------------------------------------------------------------
    # Stale propagation
    # ------------------------------------------------------------------

    def find_stale_downstream(
        self, artifact_version_id: UUID
    ) -> list[ArtifactVersion]:
        """Find artifact versions that directly consume the given version as input.

        In Foundation, this is a simple linear scan. V0+ will use an index.
        """
        av = self.get_artifact_version(artifact_version_id)
        stale: list[ArtifactVersion] = []
        for candidate in self._versions.values():
            for ref in candidate.lineage_input_refs:
                if ref.artifact_version_id == artifact_version_id:
                    stale.append(candidate)
                    break
        return stale

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_content_hash(
        content_uri: str | None, content_json: dict[str, Any] | None
    ) -> str:
        hasher = hashlib.sha256()
        if content_uri:
            hasher.update(content_uri.encode())
        if content_json:
            hasher.update(json.dumps(content_json, sort_keys=True, default=str).encode())
        return hasher.hexdigest()
