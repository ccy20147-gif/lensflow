"""Persisted current policy decisions for Architect confirmation."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from sqlalchemy.orm import Session, sessionmaker
from src.infra.db.models import ArtifactVersionModel, ResourceGrantSnapshotModel, ResourceModel, ResourceRevisionModel
from src.infra.db.session import get_session_factory

def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class ArchitectPolicyService:
    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    def evaluate(self, *, owner_scope: str, graph: dict[str, Any], registry: Any) -> dict[str, Any]:
        entitlement_errors: list[str] = []
        material_errors: list[str] = []
        cost = 0.0
        limit: float | None = None
        with self._factory() as session:
            for node in graph.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                data = node.get("data") if isinstance(node.get("data"), dict) else {}
                cfg = node.get("config") if isinstance(node.get("config"), dict) else data.get("config", {})
                cfg = cfg if isinstance(cfg, dict) else {}
                definition = registry.node_definitions.get(str(node.get("type", "")))
                metadata = definition.policy_metadata if definition else {}
                if isinstance(metadata.get("cost_estimate"), (int, float)):
                    cost += float(metadata["cost_estimate"])
                if isinstance(metadata.get("max_cost"), (int, float)):
                    value = float(metadata["max_cost"])
                    limit = value if limit is None else min(limit, value)
                for ref in cfg.get("resource_refs", []):
                    if not isinstance(ref, dict):
                        continue
                    revision = session.get(ResourceRevisionModel, ref.get("revision_id"))
                    resource = session.get(ResourceModel, revision.resource_id) if revision else None
                    grant = session.get(ResourceGrantSnapshotModel, ref.get("grant_snapshot_id")) if ref.get("grant_snapshot_id") else None
                    allowed = resource is not None and revision is not None and (resource.owner_scope == owner_scope or (grant is not None and grant.grantee_scope == owner_scope and grant.status == "active"))
                    if not allowed:
                        entitlement_errors.append(f"node:{node.get('id', '')} resource entitlement denied")
                if metadata.get("material_gate_required") and not cfg.get("material_gate_decision_id"):
                    material_errors.append(f"node:{node.get('id', '')} material rights decision missing")
        if limit is not None and cost > limit:
            entitlement_errors.append("current cost decision exceeds policy limit")
        payload = {"owner_scope": owner_scope, "policy_revision": "architect.platform.v1", "entitlement_errors": entitlement_errors, "material_errors": material_errors, "cost": {"amount": cost, "limit": limit}, "registry_schema_hash": registry.schema_hash}
        digest = _hash(payload)
        with self._factory.begin() as session:
            row = ArtifactVersionModel(artifact_version_id=uuid4(), artifact_id=uuid4(), schema_id="toonflow.architect_policy_decision", schema_version=1, owner_scope=owner_scope, content_json=payload, content_hash=digest, metadata_json={"policy_revision": "architect.platform.v1"}, created_at=datetime.now(timezone.utc))
            session.add(row)
            session.flush()
            decision_id = str(row.artifact_version_id)
        return {**payload, "decision_id": decision_id, "decision_hash": digest}
