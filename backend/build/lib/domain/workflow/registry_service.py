"""TF-WF-002: 节点注册表 Service

Manages NodeDefinitionRevision CRUD, port-type compatibility checks,
converter management, and RegistrySnapshot generation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.core.exceptions import ConflictError, NotFoundError
from src.schemas.enums import RevisionStatus
from src.schemas.models import (
    NodeDefinitionRevision,
    PortTypeRef,
    RegistrySnapshot,
)

from .node_definition import validate_definition


class RegistryService:
    """In-memory registry store for Foundation stage.

    Persistent storage (SQLAlchemy / asyncpg) will replace dicts in V0.
    """

    def __init__(self) -> None:
        # node_type_id -> { "active": NDR | None, "drafts": [NDR], "retired": [NDR] }
        self._definitions: dict[str, dict[str, Any]] = {}
        # snapshot_id -> RegistrySnapshot
        self._snapshots: dict[UUID, RegistrySnapshot] = {}
        # converter key: (from_schema_id, to_schema_id, to_schema_version) -> digest
        self._converters: dict[tuple[str, str, int], str] = {}
        self._registered_by_type: dict[str, set[str]] = {}  # type_id -> set of node_type_ids

    # ------------------------------------------------------------------
    # NodeDefinitionRevision CRUD
    # ------------------------------------------------------------------

    def register_definition(
        self,
        ndr: NodeDefinitionRevision,
    ) -> NodeDefinitionRevision:
        """Register a new NodeDefinitionRevision (draft status).

        Raises:
            ConflictError: If node_type_id + semantic_version already exists
                           and content hash differs.
        """
        validate_definition(ndr)

        existing = self._definitions.get(ndr.node_type_id, {})

        # Check for same-type+same-version content conflict
        for status_key in ("active", "drafts", "retired"):
            if status_key == "drafts":
                candidates = existing.get("drafts", [])
            else:
                candidates = [existing.get(status_key)] if existing.get(status_key) else []
            for candidate in candidates:
                if candidate is None:
                    continue
                if candidate.semantic_version == ndr.semantic_version:
                    if candidate.revision_id != ndr.revision_id:
                        # Same version, different content — reject
                        raise ConflictError(
                            message=(
                                f"节点类型 {ndr.node_type_id} 版本 {ndr.semantic_version} "
                                f"已存在且内容不同"
                            )
                        )

        if ndr.node_type_id not in self._definitions:
            self._definitions[ndr.node_type_id] = {
                "active": None,
                "drafts": [],
                "retired": [],
            }

        self._definitions[ndr.node_type_id]["drafts"].append(ndr)
        return ndr

    def activate_definition(self, node_type_id: str, revision_id: UUID) -> NodeDefinitionRevision:
        """Move a draft definition to active status.

        Raises:
            NotFoundError: If draft revision not found.
        """
        entry = self._definitions.get(node_type_id)
        if not entry:
            raise NotFoundError("NodeDefinitionRevision", node_type_id)

        # Find the draft
        draft_idx: int | None = None
        found: NodeDefinitionRevision | None = None
        for i, d in enumerate(entry["drafts"]):
            if d.revision_id == revision_id:
                draft_idx = i
                found = d
                break

        if found is None:
            raise NotFoundError("NodeDefinitionRevision (draft)", str(revision_id))

        # Remove from drafts, set as active
        entry["drafts"].pop(draft_idx)
        entry["active"] = found
        return found

    def retire_definition(self, node_type_id: str, revision_id: UUID) -> NodeDefinitionRevision:
        """Move an active definition to retired status."""
        entry = self._definitions.get(node_type_id)
        if not entry:
            raise NotFoundError("NodeDefinitionRevision", node_type_id)

        active = entry.get("active")
        if active is None or active.revision_id != revision_id:
            raise NotFoundError("NodeDefinitionRevision (active)", str(revision_id))

        entry["retired"].append(active)
        entry["active"] = None
        return active

    def get_definition(
        self, node_type_id: str, revision_id: UUID | None = None
    ) -> NodeDefinitionRevision | None:
        """Get a definition. If revision_id is None, returns the active one."""
        entry = self._definitions.get(node_type_id)
        if not entry:
            return None

        if revision_id is not None:
            for bucket_name in ("active", "drafts", "retired"):
                if bucket_name == "drafts":
                    for d in entry.get("drafts", []):
                        if d.revision_id == revision_id:
                            return d
                else:
                    candidate = entry.get(bucket_name)
                    if candidate and candidate.revision_id == revision_id:
                        return candidate
            return None

        return entry.get("active")

    def list_definitions(
        self, status: str | None = None, type_id_filter: str | None = None
    ) -> list[NodeDefinitionRevision]:
        """List all definitions, optionally filtered by status and/or type_id_filter."""
        results: list[NodeDefinitionRevision] = []
        for node_type_id, entry in self._definitions.items():
            if type_id_filter and node_type_id != type_id_filter:
                continue
            for bucket_name, bucket_status in [
                ("active", "active"),
                ("drafts", "draft"),
                ("retired", "retired"),
            ]:
                if bucket_name == "drafts":
                    for d in entry.get("drafts", []):
                        if status is None or status == "draft":
                            results.append(d)
                else:
                    item = entry.get(bucket_name)
                    if item and (status is None or status == bucket_status):
                        results.append(item)
        return results

    def query_definition_by_version(
        self, node_type_id: str, semantic_version: str
    ) -> NodeDefinitionRevision | None:
        """Find a definition by exact node_type_id + semantic_version across all statuses."""
        entry = self._definitions.get(node_type_id)
        if not entry:
            return None
        for bucket_name in ("active", "drafts", "retired"):
            if bucket_name == "drafts":
                for d in entry.get("drafts", []):
                    if d.semantic_version == semantic_version:
                        return d
            else:
                item = entry.get(bucket_name)
                if item and item.semantic_version == semantic_version:
                    return item
        return None

    # ------------------------------------------------------------------
    # Converters
    # ------------------------------------------------------------------

    def register_converter(
        self,
        from_schema_id: str,
        from_schema_version: int,
        to_schema_id: str,
        to_schema_version: int,
        executor_digest: str,
    ) -> None:
        """Register a type converter."""
        from .node_definition import validate_converter

        validate_converter(from_schema_id, from_schema_version, to_schema_id, to_schema_version, executor_digest)

        key = (from_schema_id, to_schema_id, to_schema_version)
        self._converters[key] = executor_digest

    def get_converter(
        self, from_schema_id: str, from_schema_version: int, to_schema_id: str, to_schema_version: int
    ) -> str | None:
        """Get converter digest if one exists, else None."""
        key = (from_schema_id, to_schema_id, to_schema_version)
        return self._converters.get(key)

    def list_converters(self) -> dict[str, str]:
        """List all registered converters (string-keyed for serialisation)."""
        return {f"{k[0]}→{k[1]}@v{k[2]}": v for k, v in self._converters.items()}

    # ------------------------------------------------------------------
    # Registry Snapshot
    # ------------------------------------------------------------------

    def generate_snapshot(self) -> RegistrySnapshot:
        """Freeze current active definitions + converters into an immutable snapshot."""
        node_defs: dict[str, NodeDefinitionRevision] = {}
        for node_type_id, entry in self._definitions.items():
            active = entry.get("active")
            if active is not None:
                node_defs[node_type_id] = active

        converter_revisions = self.list_converters()

        # Compute schema hash over all active definitions
        raw = []
        for ndr in sorted(node_defs.keys()):
            raw.append(ndr)
            raw.append(node_defs[ndr].model_dump_json(sort_keys=True))
        for k in sorted(converter_revisions.keys()):
            raw.append(k)
            raw.append(converter_revisions[k])

        schema_hash = hashlib.sha256("".join(raw).encode()).hexdigest()

        snapshot = RegistrySnapshot(
            snapshot_id=uuid4(),
            node_definitions=node_defs,
            converter_revisions=converter_revisions,
            schema_hash=schema_hash,
            created_at=datetime.now(timezone.utc),
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def get_snapshot(self, snapshot_id: UUID) -> RegistrySnapshot:
        """Retrieve an existing snapshot by ID.

        Raises:
            NotFoundError: If snapshot_id does not exist.
        """
        snap = self._snapshots.get(snapshot_id)
        if snap is None:
            raise NotFoundError("RegistrySnapshot", str(snapshot_id))
        return snap

    def list_snapshots(self) -> list[RegistrySnapshot]:
        """Return all snapshots, newest first."""
        return sorted(self._snapshots.values(), key=lambda s: s.created_at, reverse=True)

    # ------------------------------------------------------------------
    # Port compatibility (delegated to node_definition module)
    # ------------------------------------------------------------------

    def check_port_compatibility(
        self,
        output_port: PortTypeRef,
        input_port: PortTypeRef,
    ) -> bool:
        """Check if two ports are compatible, considering converters."""
        from .node_definition import are_ports_compatible

        converter_keys: set[tuple[str, str, int]] = set()
        for k in self._converters:
            converter_keys.add(k)

        return are_ports_compatible(output_port, input_port, converter_keys)
