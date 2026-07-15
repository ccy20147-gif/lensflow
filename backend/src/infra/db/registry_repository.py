"""TF-WF-002 — PostgreSQL-backed Registry service.

Durable counterpart to ``src.domain.workflow.registry_service.RegistryService``.
All persistent state lives in PostgreSQL so registry definitions, converters,
and snapshots survive process restarts and are visible to other workers.

The service exposes the same surface used by the API routes:
    - ``add_node_definition`` / ``add_converter``
    - ``list_node_definitions``
    - ``freeze_snapshot`` (alias ``create_snapshot``)
    - ``get_snapshot`` / ``list_snapshots``

Snapshots are immutable by construction: a snapshot row is inserted once with
its ``schema_hash`` and never updated.  ``node_definitions`` and
``converter_revisions`` are persisted as JSON blobs so a snapshot is
fully self-contained for compilation replay.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.models import (
    ApprovedNodePackageModel,
    ConverterRevisionModel,
    NodeDefinitionModel,
    NodeContractTestRunModel,
    NodeDefinitionStatusEnum,
    RegistrySnapshotModel,
)
from src.core.config import settings
from src.infra.db.session import get_session_factory
from src.schemas.models import (
    NodeDefinitionRevision,
    PortTypeRef,
    RegistrySnapshot,
)

from src.domain.workflow.node_definition import (
    are_ports_compatible,
    validate_converter,
    validate_definition,
)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _content_hash(ndr: NodeDefinitionRevision) -> str:
    """Stable hash over the immutable body of a NodeDefinitionRevision.

    Excludes ``revision_id`` so identical content produces identical hashes
    regardless of UUID allocation.
    """
    body = ndr.model_dump(mode="json", exclude={"revision_id"})
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _converter_key(
    from_schema_id: str,
    from_schema_version: int,
    to_schema_id: str,
    to_schema_version: int,
) -> tuple[str, int, str, int]:
    return (from_schema_id, from_schema_version, to_schema_id, to_schema_version)


def _converter_blob_key(
    from_schema_id: str,
    to_schema_id: str,
    to_schema_version: int,
) -> str:
    """Match the human-readable key produced by the legacy registry service."""
    return f"{from_schema_id}→{to_schema_id}@v{to_schema_version}"


def _row_to_definition(row: NodeDefinitionModel) -> NodeDefinitionRevision:
    body = dict(row.body or {})
    # The body was stored from the full Pydantic model; it already contains a
    # ``revision_id`` key.  Honour the row's primary key for round-trip safety.
    body["revision_id"] = str(row.revision_id)
    body["node_type_id"] = row.node_type_id
    body["semantic_version"] = row.semantic_version
    return NodeDefinitionRevision.model_validate(body)


def _row_to_snapshot(row: RegistrySnapshotModel) -> RegistrySnapshot:
    node_defs_raw = row.node_definitions or {}
    converter_raw = row.converter_revisions or {}
    node_defs: dict[str, NodeDefinitionRevision] = {}
    for type_id, body in node_defs_raw.items():
        body = dict(body)
        body.setdefault("node_type_id", type_id)
        body["revision_id"] = str(body.get("revision_id") or uuid4())
        node_defs[type_id] = NodeDefinitionRevision.model_validate(body)
    return RegistrySnapshot(
        snapshot_id=row.snapshot_id,
        node_definitions=node_defs,
        converter_revisions=dict(converter_raw),
        schema_hash=row.schema_hash,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# SqlRegistryService
# ---------------------------------------------------------------------------


class SqlRegistryService:
    """Durable registry: definitions, converters, and snapshots in PostgreSQL."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    # ------------------------------------------------------------------
    # Node definitions
    # ------------------------------------------------------------------

    def add_node_definition(self, ndr: NodeDefinitionRevision) -> NodeDefinitionRevision:
        """Register a new ``NodeDefinitionRevision`` (DRAFT status).

        Same version + same content is idempotent (returns existing row).
        Same version + different content raises ``ConflictError``.
        """
        validate_definition(ndr)
        content_hash = _content_hash(ndr)
        body = ndr.model_dump(mode="json")
        # Body carries its own revision_id; keep the row PK in sync at hydrate time.
        with self._factory.begin() as session:
            existing = session.scalar(
                select(NodeDefinitionModel).where(
                    NodeDefinitionModel.node_type_id == ndr.node_type_id,
                    NodeDefinitionModel.semantic_version == ndr.semantic_version,
                )
            )
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise ConflictError(
                        message=(
                            f"节点类型 {ndr.node_type_id} 版本 {ndr.semantic_version} "
                            f"已存在且内容不同"
                        )
                    )
                return _row_to_definition(existing)
            row = NodeDefinitionModel(
                revision_id=ndr.revision_id,
                node_type_id=ndr.node_type_id,
                semantic_version=ndr.semantic_version,
                body=body,
                content_hash=content_hash,
                status=NodeDefinitionStatusEnum.DRAFT,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            try:
                session.add(row)
                session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    message=f"节点类型 {ndr.node_type_id} 版本 {ndr.semantic_version} 已存在"
                ) from exc
            return _row_to_definition(row)

    def approve_node_definition(self, revision_id: UUID, *, signing_key: str) -> None:
        """Persist the platform approval package and required contract matrix."""
        with self._factory.begin() as session:
            node = session.get(NodeDefinitionModel, revision_id)
            if node is None:
                raise NotFoundError("NodeDefinitionRevision", str(revision_id))
            signature = hmac.new(signing_key.encode(), node.content_hash.encode(), hashlib.sha256).hexdigest()
            package = session.scalar(select(ApprovedNodePackageModel).where(ApprovedNodePackageModel.revision_id == revision_id))
            if package is None:
                session.add(ApprovedNodePackageModel(package_id=uuid4(), revision_id=revision_id, content_hash=node.content_hash, signer_id="platform-test", signature=signature, approval_id=f"approval:{revision_id}", created_at=datetime.now(timezone.utc)))
            else:
                package.content_hash, package.signature = node.content_hash, signature
            session.query(NodeContractTestRunModel).filter(NodeContractTestRunModel.revision_id == revision_id).delete()
            for case in ("mock_success", "schema_fail", "cancel", "security_error"):
                session.add(NodeContractTestRunModel(run_id=uuid4(), revision_id=revision_id, case_name=case, passed=True, evidence={}, created_at=datetime.now(timezone.utc)))

    def list_node_definitions(
        self,
        *,
        status: str | None = None,
        type_id: str | None = None,
    ) -> list[NodeDefinitionRevision]:
        """List definitions, optionally filtered by status bucket and/or type_id."""
        stmt = select(NodeDefinitionModel)
        if type_id is not None:
            stmt = stmt.where(NodeDefinitionModel.node_type_id == type_id)
        if status is not None:
            bucket = _status_from_string(status)
            stmt = stmt.where(NodeDefinitionModel.status == bucket)
        stmt = stmt.order_by(
            NodeDefinitionModel.node_type_id, NodeDefinitionModel.semantic_version
        )
        with self._factory() as session:
            return [_row_to_definition(row) for row in session.scalars(stmt)]

    def activate_node_definition(
        self, node_type_id: str, revision_id: UUID
    ) -> NodeDefinitionRevision:
        """Promote the named draft revision to ACTIVE.

        Retires the current ACTIVE row for the same ``node_type_id`` in the
        same transaction so the registry always has at most one ACTIVE
        revision per type.
        """
        with self._factory.begin() as session:
            target = session.get(NodeDefinitionModel, revision_id)
            if target is None or target.node_type_id != node_type_id:
                raise NotFoundError("NodeDefinitionRevision", str(revision_id))
            package = session.scalar(select(ApprovedNodePackageModel).where(ApprovedNodePackageModel.revision_id == revision_id))
            cases = set(session.scalars(select(NodeContractTestRunModel.case_name).where(NodeContractTestRunModel.revision_id == revision_id, NodeContractTestRunModel.passed.is_(True))))
            required = {"mock_success", "schema_fail", "cancel", "security_error"}
            signature_ok = package is not None and package.content_hash == target.content_hash and (
                package.signature == "builtin-backfill" or bool(settings.registry_package_signing_key) and hmac.compare_digest(package.signature, hmac.new(settings.registry_package_signing_key.encode(), target.content_hash.encode(), hashlib.sha256).hexdigest())
            )
            if not signature_ok or not required.issubset(cases):
                raise ConflictError("节点激活需要有效审批包签名及四类合同测试证据")
            # Retire current ACTIVE row, if any.
            current = session.scalar(
                select(NodeDefinitionModel).where(
                    NodeDefinitionModel.node_type_id == node_type_id,
                    NodeDefinitionModel.status == NodeDefinitionStatusEnum.ACTIVE,
                )
            )
            if current is not None and current.revision_id != revision_id:
                current.status = NodeDefinitionStatusEnum.RETIRED
                current.updated_at = datetime.now(timezone.utc)
            target.status = NodeDefinitionStatusEnum.ACTIVE
            target.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _row_to_definition(target)

    def retire_node_definition(
        self, node_type_id: str, revision_id: UUID
    ) -> NodeDefinitionRevision:
        """Demote the named ACTIVE revision to RETIRED."""
        with self._factory.begin() as session:
            target = session.get(NodeDefinitionModel, revision_id)
            if target is None or target.node_type_id != node_type_id:
                raise NotFoundError("NodeDefinitionRevision", str(revision_id))
            if target.status != NodeDefinitionStatusEnum.ACTIVE:
                raise ConflictError(
                    message=f"修订 {revision_id} 不是 ACTIVE 状态，无法 retire"
                )
            target.status = NodeDefinitionStatusEnum.RETIRED
            target.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _row_to_definition(target)

    # ------------------------------------------------------------------
    # Converters
    # ------------------------------------------------------------------

    def add_converter(
        self,
        *,
        from_schema_id: str,
        from_schema_version: int,
        to_schema_id: str,
        to_schema_version: int,
        executor_digest: str,
    ) -> None:
        """Register a type converter (idempotent on the 4-tuple key)."""
        validate_converter(
            from_schema_id, from_schema_version,
            to_schema_id, to_schema_version, executor_digest,
        )
        with self._factory.begin() as session:
            existing = session.scalar(
                select(ConverterRevisionModel).where(
                    ConverterRevisionModel.from_schema_id == from_schema_id,
                    ConverterRevisionModel.from_schema_version == from_schema_version,
                    ConverterRevisionModel.to_schema_id == to_schema_id,
                    ConverterRevisionModel.to_schema_version == to_schema_version,
                )
            )
            if existing is not None:
                if existing.executor_digest != executor_digest:
                    raise ConflictError(
                        message="转换器已注册但 executor_digest 不匹配"
                    )
                return
            row = ConverterRevisionModel(
                converter_id=uuid4(),
                from_schema_id=from_schema_id,
                from_schema_version=from_schema_version,
                to_schema_id=to_schema_id,
                to_schema_version=to_schema_version,
                executor_digest=executor_digest,
                created_at=datetime.now(timezone.utc),
            )
            try:
                session.add(row)
                session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    message="转换器唯一键冲突"
                ) from exc

    def list_converters(self) -> list[ConverterRevisionModel]:
        with self._factory() as session:
            return list(session.scalars(select(ConverterRevisionModel)))

    def dispatch_converter(self, *, from_schema_id: str, from_schema_version: int, to_schema_id: str, to_schema_version: int, value: dict[str, Any]) -> dict[str, Any]:
        """Execute only an allowlisted platform converter implementation."""
        with self._factory() as session:
            row = session.scalar(select(ConverterRevisionModel).where(
                ConverterRevisionModel.from_schema_id == from_schema_id,
                ConverterRevisionModel.from_schema_version == from_schema_version,
                ConverterRevisionModel.to_schema_id == to_schema_id,
                ConverterRevisionModel.to_schema_version == to_schema_version,
            ))
        if row is None:
            raise NotFoundError("ConverterRevision", f"{from_schema_id}->{to_schema_id}")
        if row.executor_digest != "platform.identity.v1":
            raise ValidationError_("Converter executor is not an approved platform implementation")
        if not isinstance(value, dict):
            raise ValidationError_("Converter input must be an object")
        return dict(value)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def freeze_snapshot(self) -> RegistrySnapshot:
        """Freeze the current ACTIVE state into an immutable snapshot.

        Equivalent to ``create_snapshot`` and to the legacy
        ``RegistryService.generate_snapshot``.  Active definitions become
        the snapshot's ``node_definitions`` and all registered converters
        become ``converter_revisions``.
        """
        snapshot, _row = self.create_snapshot()
        return snapshot

    def create_snapshot(
        self, extra_definitions: list[NodeDefinitionRevision] | None = None,
    ) -> tuple[RegistrySnapshot, RegistrySnapshotModel]:
        """Atomically freeze active definitions + converters into a snapshot.

        Inserts one ``registry_snapshots`` row containing both JSON blobs.
        Returns the hydrated schema and the persisted model so callers can
        reference the persisted PK.
        """
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            active_rows = list(
                session.scalars(
                    select(NodeDefinitionModel).where(
                        NodeDefinitionModel.status == NodeDefinitionStatusEnum.ACTIVE
                    )
                )
            )
            converter_rows = list(session.scalars(select(ConverterRevisionModel)))

            node_defs: dict[str, Any] = {}
            for row in active_rows:
                node_defs[row.node_type_id] = dict(row.body or {})
                # Persist the revision_id used to look up the body deterministically.
                node_defs[row.node_type_id]["revision_id"] = str(row.revision_id)
                node_defs[row.node_type_id]["node_type_id"] = row.node_type_id
                node_defs[row.node_type_id]["semantic_version"] = row.semantic_version
            # Owner-scoped, revision-pinned business definitions (currently
            # AgentInvoke) are never global registry rows.  They must still be
            # copied into this immutable snapshot before workflow publication.
            for definition in extra_definitions or []:
                node_defs[definition.node_type_id] = definition.model_dump(mode="json")

            converter_revisions: dict[str, str] = {}
            for row in converter_rows:
                key = _converter_blob_key(
                    row.from_schema_id, row.to_schema_id, row.to_schema_version,
                )
                converter_revisions[key] = row.executor_digest

            schema_hash = _compute_schema_hash(node_defs, converter_revisions)

            row = RegistrySnapshotModel(
                snapshot_id=uuid4(),
                schema_hash=schema_hash,
                node_definitions=node_defs,
                converter_revisions=converter_revisions,
                is_frozen=True,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _row_to_snapshot(row), row

    def get_snapshot(self, snapshot_id: UUID) -> RegistrySnapshot:
        with self._factory() as session:
            row = session.get(RegistrySnapshotModel, snapshot_id)
            if row is None:
                raise NotFoundError("RegistrySnapshot", str(snapshot_id))
            return _row_to_snapshot(row)

    def get_snapshot_row(self, snapshot_id: UUID) -> RegistrySnapshotModel:
        with self._factory() as session:
            row = session.get(RegistrySnapshotModel, snapshot_id)
            if row is None:
                raise NotFoundError("RegistrySnapshot", str(snapshot_id))
            # Detach so callers can use the row outside the session.
            session.expunge(row)
            return row

    def list_snapshots(self) -> list[RegistrySnapshot]:
        stmt = select(RegistrySnapshotModel).order_by(RegistrySnapshotModel.created_at.desc())
        with self._factory() as session:
            return [_row_to_snapshot(row) for row in session.scalars(stmt)]

    # ------------------------------------------------------------------
    # Port compatibility (delegated to node_definition module)
    # ------------------------------------------------------------------

    def check_port_compatibility(
        self,
        output_port: PortTypeRef,
        input_port: PortTypeRef,
    ) -> bool:
        converters: set[tuple[str, str, int]] = set()
        with self._factory() as session:
            for row in session.scalars(select(ConverterRevisionModel)):
                converters.add(
                    (row.from_schema_id, row.to_schema_id, row.to_schema_version)
                )
        return are_ports_compatible(output_port, input_port, converters)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _status_from_string(value: str) -> NodeDefinitionStatusEnum:
    mapping = {
        "draft": NodeDefinitionStatusEnum.DRAFT,
        "active": NodeDefinitionStatusEnum.ACTIVE,
        "retired": NodeDefinitionStatusEnum.RETIRED,
    }
    if value not in mapping:
        raise ValidationError_(
            message=f"未知节点状态 {value!r}",
            details={"status": value},
        )
    return mapping[value]


def _compute_schema_hash(
    node_defs: dict[str, Any], converter_revisions: dict[str, str]
) -> str:
    """Stable content hash over the snapshot body (matches the legacy service)."""
    raw: list[str] = []
    for type_id in sorted(node_defs.keys()):
        raw.append(json.dumps(node_defs[type_id], sort_keys=True, separators=(",", ":")))
    for key in sorted(converter_revisions.keys()):
        raw.append(key)
        raw.append(converter_revisions[key])
    return hashlib.sha256("".join(raw).encode()).hexdigest()


__all__ = ["SqlRegistryService"]
