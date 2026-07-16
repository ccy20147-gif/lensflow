"""Service-layer resolvers feeding the compile-time entitlement gate.

The compiler itself never touches the database.  These helpers bridge
between the HTTP / activation routes and the SQL repositories that own
the cross-owner truth:

* ``SqlArtifactRepository`` / ``ArtifactVersionModel`` for the
  canonical ArtifactVersion row, including its ``owner_scope`` and
  ``artifact_id``.
* ``SqlResourceRepository`` / ``ResourceRevisionModel`` for the
  canonical ResourceRevision row, plus its parent ``Resource`` and
  any active ``ResourceGrantSnapshot``.

The resolver implements the ``_EntitlementResolver`` protocol from
``entitlement_gate``.  Every method is shaped so the compiler can call
it without consulting the graph or the in-graph ``owner_scope``
field.  The graph's declared ``owner_scope`` is used **only** as a
hint to short-circuit shape errors — never as the basis for an allow.

Security contract (P0 fix):

* the canonical ``owner_scope`` is read from the database row;
* the graph's ``ref.owner_scope`` is **never** trusted;
* an attacker who omits or forges ``owner_scope`` cannot bypass any
  same-owner / cross-owner / grant / scope rule;
* the resolver returns one ``EntitlementSnapshot`` per ref and the
  gate treats them as authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.domain.workflow.entitlement_gate import (
    EntitlementSnapshot,
    REASON_CODES,
    _coerce_uuid,
    _normalise_target,
)


@dataclass(frozen=True)
class CompileGateInputs:
    """Minimal SQL-handle bundle for the compile gate.

    ``session_factory`` is consumed exactly once per compile request;
    the session is closed in the standard ``with`` block.  No other
    state lives on this object so it remains cheap to construct.
    """

    session_factory: Any  # sessionmaker[Session]
    actor_scope: Any  # OwnerScope


def _coerce_owner_scope(owner_scope: Any) -> str:
    """Reduce an ``OwnerScope`` (or already-flat string) to ``scoped_id``."""

    if owner_scope is None:
        return ""
    scoped = getattr(owner_scope, "scoped_id", None)
    if isinstance(scoped, str) and scoped:
        return scoped
    if isinstance(owner_scope, str):
        return owner_scope
    return str(owner_scope)


def _coerce_request_owner(actor_scope: Any) -> str:
    """Reduce the compile request's actor scope to a ``scoped_id``."""

    return _coerce_owner_scope(actor_scope)


def make_sql_entitlement_resolver(
    *,
    session_factory: Any,
    repository: Any,
    actor_scope: Any,
) -> Any:
    """Build the SQL-backed resolver the compiler consumes.

    ``actor_scope`` is the authenticated owner.  It is only used to
    identify the **request** side of the comparison; the source owner
    is always loaded from the database row.
    """

    class SqlEntitlementResolver:
        def resolve_refs(
            self,
            *,
            request_owner: str,
            refs: list[tuple[dict[str, Any], str]],
        ) -> list[EntitlementSnapshot]:
            return _resolve_refs(
                session_factory=session_factory,
                repository=repository,
                request_owner=request_owner,
                refs=refs,
            )

    return SqlEntitlementResolver()


def _resolve_refs(
    *,
    session_factory: Any,
    repository: Any,
    request_owner: str,
    refs: list[tuple[dict[str, Any], str]],
) -> list[EntitlementSnapshot]:
    """Resolve each ref independently against the canonical database rows."""

    snapshots: list[EntitlementSnapshot] = []
    with session_factory() as session:
        for ref, _path in refs:
            canonical_id, canonical_kind = _normalise_target(ref)
            if canonical_kind == "artifact_version":
                snapshots.append(_resolve_artifact_ref(
                    session=session, ref=ref, request_owner=request_owner,
                    canonical_id=canonical_id,
                ))
            elif canonical_kind == "resource_revision":
                snapshots.append(_resolve_resource_ref(
                    session=session, ref=ref, request_owner=request_owner,
                    repository=repository, canonical_id=canonical_id,
                ))
            else:
                snapshots.append(EntitlementSnapshot(
                    canonical_target=canonical_id or "",
                    canonical_kind="unknown",
                    decision="deny",
                    code=REASON_CODES["REVISION_MISMATCH"],
                    request_owner=request_owner,
                    reason="ref shape not recognised by resolver",
                ))
    return snapshots


def _resolve_artifact_ref(
    *,
    session: Any,
    ref: dict[str, Any],
    request_owner: str,
    canonical_id: str | None,
) -> EntitlementSnapshot:
    """Resolve an ``ArtifactRef`` against the canonical ArtifactVersion row.

    Owner scope comes from ``ArtifactVersion.owner_scope``; we never read
    ``ref.owner_scope``.  A cross-owner ArtifactRef is always denied —
    even when ``ref.grant_snapshot_id`` is supplied — per Master PRD §8.1.
    """

    if canonical_id is None:
        return EntitlementSnapshot(
            canonical_target="",
            canonical_kind="artifact_version",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ArtifactRef missing artifact_version_id",
        )

    # Late imports keep this module resolvable without a live DB at
    # collection time (unit tests rely on that property).
    from src.infra.db.models import ArtifactVersionModel

    try:
        version_uuid = UUID(canonical_id)
    except (TypeError, ValueError):
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="artifact_version",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ArtifactRef artifact_version_id is not a UUID",
        )

    artifact_row = session.get(ArtifactVersionModel, version_uuid)
    if artifact_row is None:
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="artifact_version",
            decision="deny",
            code=REASON_CODES["TARGET_NOT_FOUND"],
            request_owner=request_owner,
            reason="ArtifactVersion 不存在",
        )

    declared_artifact_id = _coerce_uuid(ref.get("artifact_id"))
    if declared_artifact_id is not None and str(declared_artifact_id) != str(artifact_row.artifact_id):
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="artifact_version",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ArtifactRef.artifact_id 与 ArtifactVersion.artifact_id 不一致",
        )

    source_owner = str(artifact_row.owner_scope or "")
    if source_owner == request_owner:
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="artifact_version",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner=source_owner,
            request_owner=request_owner,
            reason="ArtifactVersion owner 与当前 actor 一致",
            details={"artifact_version_id": canonical_id, "artifact_id": str(artifact_row.artifact_id)},
        )

    # Cross-owner ArtifactRef is forbidden regardless of grant fields.
    has_grant_field = bool(ref.get("grant_snapshot_id"))
    code = (
        REASON_CODES["CROSS_OWNER_ARTIFACT_GRANT_FIELD"]
        if has_grant_field
        else REASON_CODES["CROSS_OWNER_ARTIFACT"]
    )
    return EntitlementSnapshot(
        canonical_target=canonical_id,
        canonical_kind="artifact_version",
        decision="deny",
        code=code,
        source_owner=source_owner,
        request_owner=request_owner,
        reason="跨 owner ArtifactRef 不允许在 CompiledExecutionPlan 中固定消费",
        details={"artifact_version_id": canonical_id, "artifact_id": str(artifact_row.artifact_id)},
    )


def _resolve_resource_ref(
    *,
    session: Any,
    ref: dict[str, Any],
    request_owner: str,
    repository: Any,
    canonical_id: str | None,
) -> EntitlementSnapshot:
    """Resolve a ``ResourceRef`` against the canonical Resource + Revision rows.

    Steps:

    1. Load the ``ResourceRevisionModel`` by ``canonical_id``.
    2. Verify ``ref.resource_id`` matches the parent Resource id;
       a mismatch is a hard deny (it implies a forged or stale graph).
    3. Compare the parent ``Resource.owner_scope`` against the
       authenticated actor scope.  Same-owner refs are always allowed
       without a grant; cross-owner refs MUST carry an active,
       revision-matching, action-scope-sufficient ``grant_snapshot_id``.
    """

    if canonical_id is None:
        return EntitlementSnapshot(
            canonical_target="",
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ResourceRef missing resource_revision_id",
        )

    from src.infra.db.models import ResourceModel, ResourceRevisionModel

    try:
        revision_uuid = UUID(canonical_id)
    except (TypeError, ValueError):
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ResourceRef revision_id is not a UUID",
        )

    revision = session.get(ResourceRevisionModel, revision_uuid)
    if revision is None:
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["TARGET_NOT_FOUND"],
            request_owner=request_owner,
            reason="ResourceRevision 不存在",
        )

    declared_resource_id = _coerce_uuid(ref.get("resource_id"))
    if declared_resource_id is not None and str(declared_resource_id) != str(revision.resource_id):
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            request_owner=request_owner,
            reason="ResourceRef.resource_id 与 ResourceRevision.resource_id 不一致",
            details={"resource_id": str(revision.resource_id), "revision_id": canonical_id},
        )

    resource = session.get(ResourceModel, revision.resource_id)
    if resource is None:
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["TARGET_NOT_FOUND"],
            request_owner=request_owner,
            reason="Resource 不存在",
        )

    source_owner = str(resource.owner_scope or "")
    action_scope = str(ref.get("role") or "reference")
    grant_id_raw = ref.get("grant_snapshot_id")
    grant_id = _coerce_uuid(grant_id_raw)

    if source_owner == request_owner:
        # Same-owner ResourceRef: no grant required, the canonical
        # Resource row already binds it to the requester.
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner=source_owner,
            request_owner=request_owner,
            action_scope=action_scope,
            reason="Resource owner 与当前 actor 一致",
            details={"resource_id": str(resource.resource_id), "revision_id": canonical_id},
        )

    # Cross-owner ResourceRef: must have an active grant_snapshot_id
    # that maps to this exact revision and supplies the requested
    # action scope.  ``evaluate_entitlement`` is the canonical checker.
    if grant_id is None:
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="deny",
            code=REASON_CODES["MISSING_GRANT"],
            source_owner=source_owner,
            request_owner=request_owner,
            action_scope=action_scope,
            reason="跨 owner ResourceRef 必须携带有效 GrantSnapshot",
            details={"resource_id": str(resource.resource_id), "revision_id": canonical_id},
        )

    from src.schemas.models import OwnerScope as _OwnerScope
    requester = _OwnerScope(
        kind=request_owner.split(":", 1)[0] if ":" in request_owner else "user",
        id=UUID(request_owner.split(":", 1)[1]) if ":" in request_owner else UUID(int=0),
    )
    decision = repository.evaluate_entitlement(
        resource_id=resource.resource_id,
        revision_id=revision_uuid,
        requester=requester,
        action=action_scope,
        grant_snapshot_id=grant_id,
    )
    if decision.decision == "allow":
        return EntitlementSnapshot(
            canonical_target=canonical_id,
            canonical_kind="resource_revision",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner=source_owner,
            request_owner=request_owner,
            grant_snapshot_id=grant_id,
            action_scope=action_scope,
            reason=decision.reason or "active grant_snapshot 授权",
            details={"resource_id": str(resource.resource_id), "revision_id": canonical_id},
        )

    reason_text = decision.reason or "Entitlement denied"
    code = REASON_CODES["DECISION_DENY"]
    lowered = reason_text.lower()
    if "缺少 grant" in reason_text:
        code = REASON_CODES["MISSING_GRANT"]
    elif "revoked" in lowered or "不可用" in reason_text:
        code = REASON_CODES["REVOKED_GRANT"]
    elif "scope" in lowered or "范围" in reason_text or "action" in lowered:
        code = REASON_CODES["SCOPE_INSUFFICIENT"]
    elif "active" in lowered or "不存在" in reason_text:
        code = REASON_CODES["REVISION_MISMATCH"]
    return EntitlementSnapshot(
        canonical_target=canonical_id,
        canonical_kind="resource_revision",
        decision="deny",
        code=code,
        source_owner=source_owner,
        request_owner=request_owner,
        grant_snapshot_id=grant_id,
        action_scope=action_scope,
        reason=reason_text,
        details={"resource_id": str(resource.resource_id), "revision_id": canonical_id},
    )


__all__ = [
    "CompileGateInputs",
    "make_sql_entitlement_resolver",
]