"""Compile-gate entitlement scanner for TF-WF-003 + TF-SEC-001.

The compile-time gate owns the **only** authority on whether a graph
reference may appear in a CompiledExecutionPlan.  It is deliberately
split into two roles:

* the **static scanner** in this module — discovers candidate refs in
  the graph, raises diagnostics for ``latest`` markers and
  ``secret``-shaped strings, and routes each ref individually to the
  resolver.  It NEVER decides allow / deny from the graph; every
  authorization decision comes from the resolver.
* the **resolver** supplied by the application service — the only
  piece of code allowed to consult PostgreSQL.  It loads the canonical
  ``ArtifactVersion`` / ``Resource`` / ``ResourceRevision`` /
  ``ResourceGrantSnapshot`` rows, and emits one
  ``EntitlementSnapshot`` per ref.

The reason for the split: the previous gate trusted ``ref.owner_scope``
(an attacker-controlled field).  The new contract is that the
**resolver** is the single source of truth.  Even if the graph lies
about ownership, the resolver reads the canonical row and the gate
denies the cross-owner attempt.  This module never falls back to a
graph-declared owner scope to make a positive decision.

All decisions are returned as ``EntitlementSnapshot`` records with
``canonical_target`` (the immutable id the plan should pin),
``canonical_kind`` (``"artifact_version"`` / ``"resource_revision"``),
``source_owner`` (loaded from the canonical row), ``request_owner``,
``grant_snapshot_id`` when applicable, and a stable ``code`` from
:data:`REASON_CODES`.  Diagnostic messages never include the raw
attacker payload, the matched secret, or the canonical row's content.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol


# ---------------------------------------------------------------------------
# Reason code vocabulary
# ---------------------------------------------------------------------------

REASON_CODES: dict[str, str] = {
    "CROSS_OWNER_ARTIFACT": "WF_INPUT_CROSS_OWNER_ARTIFACT",
    "CROSS_OWNER_ARTIFACT_GRANT_FIELD": "WF_INPUT_CROSS_OWNER_ARTIFACT_WITH_GRANT",
    "MISSING_GRANT": "WF_ENTITLEMENT_MISSING_GRANT",
    "REVOKED_GRANT": "WF_ENTITLEMENT_REVOKED",
    "SCOPE_INSUFFICIENT": "WF_ENTITLEMENT_SCOPE_INSUFFICIENT",
    "REVISION_MISMATCH": "WF_ENTITLEMENT_REVISION_MISMATCH",
    "TARGET_NOT_FOUND": "WF_ENTITLEMENT_TARGET_NOT_FOUND",
    "DECISION_DENY": "WF_ENTITLEMENT_DENIED",
    "DECISION_UNKNOWN": "WF_ENTITLEMENT_UNKNOWN",
    "DECISION_ALLOW": "WF_ENTITLEMENT_ALLOW",
    "LATEST_MARKER": "WF_INPUT_LATEST_MARKER",
    "SECRET_PLAINTEXT": "WF_INPUT_SECRET_PLAINTEXT",
    "REQUIRED_CONTROL_BLOCKED": "WF_CAPABILITY_BLOCKED",
    "OPTIONAL_CONTROL_DEGRADED": "WF_CAPABILITY_DEGRADED",
    "OPTIONAL_CONTROL_WARNING": "WF_CAPABILITY_WARNING",
}


# Reusable compiled regexes for secret-shape detection.  We avoid putting
# live secrets into plans/diagnostics, so the scanner never returns the
# matched text — only the path + a stable reason code.
_SECRET_SHAPE_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}"
    r"|AIza[\w-]{16,}"
    r"|(?:api[_-]?key|access[_-]?token|bearer)\s*[:=]\s*[A-Za-z0-9._\-]{6,}"
    r"|-----BEGIN [A-Z ]+ PRIVATE KEY-----)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntitlementSnapshot:
    """Canonical record of a single gate decision.

    The fields below are the **only** authoritative input the compiler
    uses to determine whether a ref is allowed in the plan:

    * ``canonical_target`` is the immutable id from the canonical row
      (ArtifactVersion or ResourceRevision).  When the canonical row
      cannot be loaded, ``canonical_target`` carries the raw id the
      resolver attempted to verify, and the decision is always deny.
    * ``canonical_kind`` distinguishes the two kinds so the activation
      / runtime paths can re-bind the snapshot without scanning.
    * ``source_owner`` is the owner recorded on the canonical row.
      This is the **only** owner value the gate trusts.  A graph that
      declares any other ``owner_scope`` is ignored.
    * ``request_owner`` is the authenticated actor performing the
      compile.  Comparing it to ``source_owner`` decides same- vs
      cross-owner.
    * ``decision`` is ``"allow"`` or ``"deny"``; ``code`` is one of
      :data:`REASON_CODES`.  ``details`` only carries stable identifiers
      so the gate does not leak evidence bodies into the plan JSON.
    """

    canonical_target: str
    canonical_kind: str  # "artifact_version" | "resource_revision" | "unknown"
    decision: str  # "allow" | "deny"
    code: str
    source_owner: str = ""
    request_owner: str = ""
    grant_snapshot_id: uuid.UUID | None = None
    action_scope: str = ""
    reason: str = ""
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecretFinding:
    """A secret-shaped value discovered at a JSON path inside the graph."""

    path: str
    reason: str


@dataclass(frozen=True)
class CapabilityDecision:
    """Per-control ProviderCompilationReport item outcome.

    Mirrors Master PRD §8.4 verbatim.  ``outcome`` is one of the frozen
    enumeration (``applied | transformed | degraded | ignored_with_warning
    | blocked``); the compiler MUST NOT introduce another value.
    """

    control_id: str
    required: bool
    unsupported_policy: str  # ``block | degrade | ignore_with_warning``
    outcome: str  # frozen outcome enum
    reason_code: str = ""
    semantic_loss: bool = False


class _EntitlementResolver(Protocol):
    """Protocol for the resolver the compiler consumes.

    The resolver is the **only** code allowed to load canonical rows.
    Each call must produce one ``EntitlementSnapshot`` per ref supplied;
    the gate treats the returned snapshots as authoritative and never
    re-derives allow/deny from graph fields.
    """

    def resolve_refs(
        self,
        *,
        request_owner: str,
        refs: list[tuple[dict[str, Any], str]],
    ) -> list[EntitlementSnapshot]: ...


# ---------------------------------------------------------------------------
# Static scanner
# ---------------------------------------------------------------------------


def _walk(value: Any, path: str) -> Iterable[tuple[Any, str]]:
    """Yield every dict/list pair reachable from ``value`` with its path."""

    yield value, path
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _walk(nested, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk(nested, f"{path}[{index}]")


def _as_ref_candidates(graph: dict[str, Any]) -> Iterable[tuple[dict[str, Any], str]]:
    """Yield every JSON object that looks like an ArtifactRef or ResourceRef.

    We intentionally err on the side of *including* anything that names
    the recognised keys — the resolver is the source of truth, this
    function just collects plausible targets.
    """

    for value, path in _walk(graph, ""):
        if not isinstance(value, dict):
            continue
        if "resource_id" in value and "revision_id" in value:
            yield value, path
        elif "artifact_id" in value and "artifact_version_id" in value:
            yield value, path


def _is_latest_marker(ref: Mapping[str, Any]) -> bool:
    if ref.get("latest_at_compile") is True:
        return True
    for sentinel in ("revision_id", "artifact_version_id"):
        if ref.get(sentinel) == "latest":
            return True
    return False


def _scan_secrets(graph: dict[str, Any]) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for value, path in _walk(graph, ""):
        if isinstance(value, str) and _SECRET_SHAPE_RE.search(value):
            findings.append(SecretFinding(path=path, reason=REASON_CODES["SECRET_PLAINTEXT"]))
    return findings


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    """Best-effort UUID parser.  Returns ``None`` for any non-canonical value.

    The resolver is the single source of truth and will return its own
    diagnostics for malformed ids; we never raise here.
    """

    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _normalise_target(ref: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(canonical_id, kind)`` extracted from a ref shape.

    The values here are NOT trusted.  The resolver must reload the row
    and confirm.  ``None`` is returned when the ref lacks a usable id.
    """

    if "artifact_id" in ref and "artifact_version_id" in ref:
        version_id = _coerce_uuid(ref.get("artifact_version_id"))
        if version_id is not None:
            return str(version_id), "artifact_version"
        return None, "artifact_version"
    if "resource_id" in ref and "revision_id" in ref:
        revision_id = _coerce_uuid(ref.get("revision_id"))
        if revision_id is not None:
            return str(revision_id), "resource_revision"
        return None, "resource_revision"
    return None, "unknown"


def _split_refs(refs_with_paths: list[tuple[dict[str, Any], str]]) -> tuple[
    list[tuple[dict[str, Any], str]],
    list[tuple[dict[str, Any], str]],
    list[tuple[dict[str, Any], str]],
]:
    """Partition discovered refs into three queues for the resolver.

    The split exists purely so the resolver can short-circuit obvious
    shape errors (latest marker, no usable id) without first consulting
    PostgreSQL — these are programmer errors, not policy decisions.
    """

    valid: list[tuple[dict[str, Any], str]] = []
    latest: list[tuple[dict[str, Any], str]] = []
    shape_error: list[tuple[dict[str, Any], str]] = []
    for ref, path in refs_with_paths:
        if _is_latest_marker(ref):
            latest.append((ref, path))
            continue
        canonical_id, _kind = _normalise_target(ref)
        if canonical_id is None:
            shape_error.append((ref, path))
            continue
        valid.append((ref, path))
    return valid, latest, shape_error


# Sentinel reason codes a unit-test resolver can return without loading
# PostgreSQL.  Production HTTP / activation paths must inject the SQL
# resolver; without one, the compile call fails closed (see
# ``scan_graph`` below).

_TEST_FAIL_CLOSED_REASON = "WF_ENTITLEMENT_RESOLVER_MISSING"


def scan_graph(
    *,
    request_owner: str,
    graph: dict[str, Any],
    resolver: _EntitlementResolver | None,
) -> tuple[list[EntitlementSnapshot], list[SecretFinding], list[dict[str, Any]]]:
    """Compile-time gate: scan the graph, route every ref to the resolver.

    Returns:

    * ``snapshots`` — one ``EntitlementSnapshot`` per ref encountered
      (or one fail-closed snapshot per shape-error ref).  These are
      the only authoritative allow/deny records the compiler uses.
    * ``secret_findings`` — every secret-shaped string found in the
      graph.  The compiler turns these into error diagnostics.
    * ``diagnostics`` — ``latest`` markers, shape errors, and any
      fail-closed deny snapshots projected as UI-friendly
      diagnostics.

    The static scanner never allows a ref.  When ``resolver`` is
    ``None`` every valid ref is denied with a fail-closed diagnostic;
    this protects unit-test fixtures and any future caller that
    forgets to inject a SQL resolver.
    """

    snapshots: list[EntitlementSnapshot] = []
    diagnostics: list[dict[str, Any]] = []

    refs_with_paths = list(_as_ref_candidates(graph))
    valid_refs, latest_refs, shape_refs = _split_refs(refs_with_paths)

    # Latest markers are always hard errors regardless of owner.
    for ref, path in latest_refs:
        diagnostics.append({
            "severity": "error",
            "location": f"input_ref:{path}",
            "code": REASON_CODES["LATEST_MARKER"],
            "message": "CompiledExecutionPlan cannot reference latest_at_compile markers",
            "remediation": "Resolve the marker to a fixed resource_revision_id or artifact_version_id before activation.",
        })
        kind = "artifact_version" if "artifact_id" in ref else "resource_revision"
        snapshots.append(EntitlementSnapshot(
            canonical_target=str(ref.get("artifact_version_id") or ref.get("revision_id") or ""),
            canonical_kind=kind,
            decision="deny",
            code=REASON_CODES["LATEST_MARKER"],
            source_owner="",
            request_owner=request_owner,
            reason="latest marker is forbidden in CompiledExecutionPlan",
        ))

    # Shape errors (missing or malformed id) are programmer errors but
    # the gate still emits a deny snapshot so the compiler can produce
    # a single, consistent diagnostic surface.
    for ref, path in shape_refs:
        canonical_id, canonical_kind = _normalise_target(ref)
        diagnostics.append({
            "severity": "error",
            "location": f"input_ref:{path}",
            "code": REASON_CODES["REVISION_MISMATCH"],
            "message": "ArtifactRef/ResourceRef 缺少可解析的固定 id",
            "remediation": "Provide a fixed artifact_version_id or resource_revision_id (UUID).",
        })
        snapshots.append(EntitlementSnapshot(
            canonical_target=canonical_id or "",
            canonical_kind=canonical_kind or "unknown",
            decision="deny",
            code=REASON_CODES["REVISION_MISMATCH"],
            source_owner="",
            request_owner=request_owner,
            reason="missing or malformed id in ref",
        ))

    # Authoritative decisions live in the resolver.  When no resolver
    # is injected we fail closed: every valid ref becomes a deny.
    if resolver is None:
        for ref, path in valid_refs:
            canonical_id, canonical_kind = _normalise_target(ref)
            diagnostics.append({
                "severity": "error",
                "location": f"input_ref:{path}",
                "code": _TEST_FAIL_CLOSED_REASON,
                "message": "Compile invoked without an entitlement resolver; the gate fails closed.",
                "remediation": "Inject a SQL-backed entitlement resolver before activating this plan.",
            })
            snapshots.append(EntitlementSnapshot(
                canonical_target=canonical_id or "",
                canonical_kind=canonical_kind or "unknown",
                decision="deny",
                code=_TEST_FAIL_CLOSED_REASON,
                source_owner="",
                request_owner=request_owner,
                reason="no entitlement resolver wired",
            ))
    else:
        resolved = resolver.resolve_refs(request_owner=request_owner, refs=valid_refs)
        snapshots.extend(resolved)
        for snap, (ref, path) in zip(resolved, valid_refs):
            if snap.decision == "deny":
                # Honour the resolver's reason code.  This is the only
                # path that ever emits a deny for a ref whose id parses
                # cleanly — i.e. it represents a real authorization
                # decision against the canonical database row.
                diagnostics.append({
                    "severity": "error",
                    "location": f"input_ref:{path}",
                    "code": snap.code,
                    "message": snap.reason or "Entitlement denied for ref",
                    "remediation": "Resolve the grant or material-rights gate and recompile.",
                })

    # Secrets are an orthogonal concern — every occurrence is an
    # error regardless of who owns the surrounding ref.
    secret_findings = _scan_secrets(graph)
    for finding in secret_findings:
        diagnostics.append({
            "severity": "error",
            "location": finding.path,
            "code": finding.reason,
            "message": "图/配置包含明文凭证或 secret 字符串，必须改为 CredentialBinding 引用",
            "remediation": "Use a CredentialBinding reference instead of inline credentials.",
        })

    return snapshots, secret_findings, diagnostics


__all__ = [
    "EntitlementSnapshot",
    "SecretFinding",
    "CapabilityDecision",
    "scan_graph",
    "REASON_CODES",
]