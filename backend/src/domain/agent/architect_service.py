"""Host-owned, auditable Workflow Architect proposal lifecycle.

The Architect Agent never writes a draft.  Its typed proposal is stored as an
immutable artifact; this service validates it, records a validation result and
applies it only through WorkflowDraft CAS after an owner confirmation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError, ValidationError_
from src.infra.db.models import ArtifactVersionModel
from src.infra.db.session import get_session_factory
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.agent_repository import SqlAgentRepository
from src.domain.agent.invocation_service import AgentInvocationService
from src.domain.workflow.compiler import CompilationError, WorkflowCompiler
from src.infra.db.registry_repository import SqlRegistryService
from src.schemas.models import OwnerScope
from src.domain.agent.architect_policy_service import ArchitectPolicyService

_SCHEMA = "toonflow.workflow_change_proposal"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ArchitectService:
    def __init__(self, factory: sessionmaker[Session] | None = None, *, invocation_service: AgentInvocationService | None = None) -> None:
        self._factory = factory or get_session_factory()
        self._workflows = SqlWorkflowService(self._factory)
        self._agents = SqlAgentRepository(self._factory)
        self._invocations = invocation_service

    def generate(
        self, *, workflow_id: UUID, owner_scope: str, base_draft_hash: str,
        intent: str, node_run_attempt_id: UUID,
    ) -> dict[str, Any]:
        """Generate a proposal only through a fixed managed AgentRevision.

        The browser never supplies graph operations.  The runtime pins the
        managed revision and AtlasCloud result before this service validates
        and stores the owner-confirmable proposal artifact.
        """
        workflow = self._workflows.get_workflow(workflow_id)
        if workflow.owner_scope.scoped_id != owner_scope:
            raise ValidationError_("Only the workflow owner may create Architect proposals")
        draft = self._workflows.get_draft(workflow_id)
        if draft.graph_hash != base_draft_hash:
            raise ConflictError("Architect proposal base_draft_hash is stale")
        kind, raw_id = owner_scope.split(":", 1)
        owner = OwnerScope(kind=kind, id=UUID(raw_id))
        revision = self._managed_revision(owner_scope)
        registry = SqlRegistryService(self._factory).freeze_snapshot()
        input_snapshot = self._architect_input_snapshot(
            workflow_id=workflow_id, base_draft_hash=base_draft_hash,
            intent=intent, draft_graph=dict(draft.graph or {}), registry=registry,
        )
        invocations = self._invocations or AgentInvocationService(self._factory)
        result = invocations.execute(
            agent_revision_id=revision,
            owner_scope=owner,
            node_run_attempt_id=node_run_attempt_id,
            typed_inputs=input_snapshot,
            idempotency_key=f"architect:{workflow_id}:{base_draft_hash}",
        )
        if result["status"] == "unknown":
            return {"state": "unknown", "provider_attempt_id": str(result["provider_attempt_id"])}
        artifact_id = result["artifact_version_ids"][0]
        with self._factory() as s:
            artifact = s.get(ArtifactVersionModel, artifact_id)
            if artifact is None:
                raise NotFoundError("ArchitectAgentOutput", str(artifact_id))
            generated = dict(artifact.content_json or {})
        operations = generated.get("operations")
        if not isinstance(operations, list):
            raise ValidationError_("Architect generated output lacks typed operations")
        return self._store_proposal(workflow_id=workflow_id, owner_scope=owner_scope,
            base_draft_hash=base_draft_hash, intent=intent, operations=operations,
            provenance={"agent_revision_id": str(revision), "agent_artifact_version_id": str(artifact_id)},
            input_snapshot=input_snapshot)

    @staticmethod
    def _architect_input_snapshot(*, workflow_id: UUID, base_draft_hash: str, intent: str, draft_graph: dict[str, Any], registry: Any) -> dict[str, Any]:
        """Freeze the complete minimum-visible Architect input for audit/replay."""
        definitions = []
        for definition in registry.node_definitions.values():
            definitions.append({
                "node_type_id": definition.node_type_id,
                "revision_id": str(definition.revision_id),
                "input_ports": [item.model_dump(mode="json") for item in definition.input_ports],
                "output_ports": [item.model_dump(mode="json") for item in definition.output_ports],
            })
        return {
            "workflow_id": str(workflow_id), "base_draft_hash": base_draft_hash,
            "intent": intent, "intent_hash": _hash(intent), "draft_graph": draft_graph,
            "registry_snapshot_ref": {"snapshot_id": str(registry.snapshot_id), "schema_hash": registry.schema_hash, "definitions": definitions},
            "constraints": {}, "visible_resource_refs": [],
        }

    def _managed_revision(self, owner_scope: str) -> UUID:
        """Return the immutable built-in Architect revision for this owner."""
        name = "__toonflow_workflow_architect__"
        definitions = self._agents.list_definitions(owner_scope=owner_scope)
        definition = next((item for item in definitions if item.name == name and item.agent_kind == "managed_preset"), None)
        if definition is None:
            definition = self._agents.create_definition(name=name, description="System-managed typed workflow proposal agent", agent_kind="managed_preset", owner_scope=owner_scope)
        revisions = self._agents.list_revisions(definition.agent_id)
        active = next((item for item in revisions if item.revision_status.value == "active"), None)
        if active is not None:
            return active.revision_id
        body = {
            "output_schema_ref": "toonflow.workflow_change_proposal.v1",
            "output_schema": {
                "type": "object", "properties": {"operations": {"type": "array", "items": {"type": "object"}}},
                "required": ["operations"], "additionalProperties": False,
            },
            "sop_steps": [{"step_id": "propose", "instruction": "Produce a conservative typed workflow change proposal with only explicit pinned capabilities."}],
            "execution_policy": {"provider_ref": "atlascloud/default", "max_attempts": 1},
        }
        draft = self._agents.create_revision(definition.agent_id, body)
        return self._agents.promote_revision(draft.revision_id).revision_id

    def create(
        self,
        *,
        workflow_id: UUID,
        owner_scope: str,
        base_draft_hash: str,
        intent: str,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._store_proposal(workflow_id=workflow_id, owner_scope=owner_scope,
            base_draft_hash=base_draft_hash, intent=intent, operations=operations)

    def _store_proposal(
        self, *, workflow_id: UUID, owner_scope: str, base_draft_hash: str,
        intent: str, operations: list[dict[str, Any]], provenance: dict[str, str] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow = self._workflows.get_workflow(workflow_id)
        if workflow.owner_scope.scoped_id != owner_scope:
            raise ValidationError_(
                "Only the workflow owner may create Architect proposals"
            )
        draft = self._workflows.get_draft(workflow_id)
        if draft.graph_hash != base_draft_hash:
            raise ConflictError("Architect proposal base_draft_hash is stale")
        self._validate_operations(operations)
        proposal_id = uuid4()
        payload = {
            "proposal_id": str(proposal_id),
            "workflow_id": str(workflow_id),
            "owner_scope": owner_scope,
            "base_draft_hash": base_draft_hash,
            "intent": intent,
            "input_snapshot": input_snapshot or self._architect_input_snapshot(
                workflow_id=workflow_id, base_draft_hash=base_draft_hash, intent=intent,
                draft_graph=dict(draft.graph or {}), registry=SqlRegistryService(self._factory).freeze_snapshot(),
            ),
            "operations": operations,
            "state": "generated",
            "validation": self._validate_current(
                workflow_id=workflow_id, owner_scope=owner_scope, graph=self._apply_operations(
                    dict(draft.graph or {}), operations
                ), base_draft_hash=base_draft_hash, operations=operations,
            ),
        }
        if provenance:
            payload["generation"] = provenance
        return self._append(proposal_id, payload)

    def latest(self, proposal_id: UUID) -> dict[str, Any]:
        with self._factory() as s:
            row = s.scalar(
                select(ArtifactVersionModel)
                .where(
                    ArtifactVersionModel.artifact_id == proposal_id,
                    ArtifactVersionModel.schema_id == _SCHEMA,
                )
                .order_by(ArtifactVersionModel.created_at.desc())
                .limit(1)
            )
            if row is None:
                raise NotFoundError("WorkflowChangeProposal", str(proposal_id))
            return dict(row.content_json or {})

    def diff(self, proposal_id: UUID) -> dict[str, Any]:
        proposal = self.latest(proposal_id)
        counts: dict[str, int] = {}
        for operation in proposal["operations"]:
            kind = operation["op"]
            counts[kind] = counts.get(kind, 0) + 1
        return {
            "proposal_id": str(proposal_id),
            "base_draft_hash": proposal["base_draft_hash"],
            "operations": proposal["operations"],
            "summary": counts,
            "validation": proposal["validation"],
        }

    def apply(
        self,
        *,
        proposal_id: UUID,
        owner_scope: str,
        base_draft_hash: str,
        validated_plan_hash: str,
        idempotency_key: str = "legacy-service-call",
    ) -> dict[str, Any]:
        proposal = self.latest(proposal_id)
        if proposal["owner_scope"] != owner_scope:
            raise ValidationError_("Only the proposal owner may approve it")
        if proposal["state"] == "applied":
            approved_key = str((proposal.get("approval") or {}).get("idempotency_key", ""))
            if approved_key and approved_key != idempotency_key:
                raise ConflictError("Architect proposal was already applied with a different idempotency key")
            return proposal
        if proposal["base_draft_hash"] != base_draft_hash:
            raise ConflictError("Architect proposal confirmation is stale")
        workflow_id = UUID(proposal["workflow_id"])
        workflow = self._workflows.get_workflow(workflow_id)
        if workflow.owner_scope.scoped_id != owner_scope:
            raise ValidationError_("Workflow owner changed")
        draft = self._workflows.get_draft(workflow_id)
        if draft.graph_hash != base_draft_hash:
            raise ConflictError("Workflow draft changed after proposal validation")
        graph = self._apply_operations(dict(draft.graph or {}), proposal["operations"])
        # Proposal-time validation is advisory only.  Confirmation re-runs every
        # host-owned gate against the current registry, entitlement and budget
        # view, then persists that evidence before it can mutate the draft.
        validation = self._validate_current(
            workflow_id=workflow_id, owner_scope=owner_scope, graph=graph,
            base_draft_hash=base_draft_hash, operations=proposal["operations"],
        )
        validated = dict(proposal)
        validated["state"] = "valid" if validation["state"] == "valid" else "invalid"
        validated["validation"] = validation
        self._append(proposal_id, validated)
        if validation["state"] != "valid":
            raise ConflictError("Architect proposal current validation is blocking", details=validation)
        if validation["validated_plan_hash"] != validated_plan_hash:
            raise ConflictError("Architect proposal validation changed; reload the confirmation report")
        saved = self._workflows.save_draft(
            workflow_id,
            graph,
            dict(draft.config or {}),
            dict(draft.layout or {}),
            base_draft_hash,
        )
        applied = dict(proposal)
        applied["state"] = "applied"
        applied["applied_draft_hash"] = saved.graph_hash
        applied["approval"] = {
            "owner_scope": owner_scope,
            "idempotency_key": idempotency_key,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        return self._append(proposal_id, applied)

    def _validate_current(
        self, *, workflow_id: UUID, owner_scope: str, graph: dict[str, Any],
        base_draft_hash: str, operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the confirmation-time host gates and return durable evidence.

        The platform currently has no separate billing/rights service, so this
        method makes the policy inputs explicit and fails closed when a graph
        asks for a resource or material gate that cannot be evidenced.  The
        resulting report is deliberately serialisable: it is stored with every
        validation transition and rendered by the canvas before confirmation.
        """
        schema_errors: list[str] = []
        entitlement_errors: list[str] = []
        material_errors: list[str] = []
        registry = SqlRegistryService(self._factory).freeze_snapshot()
        decision = ArchitectPolicyService(self._factory).evaluate(owner_scope=owner_scope, graph=graph, registry=registry)
        node_defs = registry.node_definitions
        # Empty installations cannot compile a meaningful registered graph;
        # retain the legacy blank-draft proposal path, but flag it rather than
        # inventing synthetic definitions.  Once a registry exists every node
        # is strictly pinned and compiled against this fresh snapshot.
        if node_defs:
            try:
                # A proposal has no WorkflowRevision yet.  Use a deterministic
                # virtual revision solely for the pure compile hash so the
                # same current evidence yields the same confirmation token.
                plan = WorkflowCompiler().compile(
                    workflow_revision_id=uuid5(NAMESPACE_URL, f"architect:{workflow_id}:{base_draft_hash}"), graph=graph,
                    registry_snapshot=registry,
                )
                # Compiler plan ids include a snapshot row id.  Use a semantic
                # hash for the confirmation token so an unchanged active
                # registry does not invalidate an already rendered review.
                compiled_plan_hash = _hash({
                    "resolved_graph": plan.resolved_graph,
                    "registry_schema_hash": registry.schema_hash,
                    "compiler_version": plan.compiler_version,
                })
            except CompilationError as exc:
                schema_errors.extend(
                    str(d.get("message", "compile error"))
                    for d in exc.details.get("diagnostics", [])
                )
                compiled_plan_hash = ""
        else:
            compiled_plan_hash = _hash({"base": base_draft_hash, "operations": operations, "registry": "empty"})
        estimated_cost = float(decision["cost"]["amount"])
        max_cost: float | None = None
        for node in graph.get("nodes", []):
            config = node.get("config", node.get("data", {})) if isinstance(node, dict) else {}
            config = config if isinstance(config, dict) else {}
            max_cost = decision["cost"]["limit"]
            for raw_ref in config.get("artifact_refs", []):
                if isinstance(raw_ref, dict) and raw_ref.get("owner_scope") not in {None, owner_scope}:
                    entitlement_errors.append("Cross-owner ArtifactRef requires a granted fixed ResourceRef")
            if config.get("material_gate_required") and not config.get("material_gate_evidence"):
                material_errors.append(f"node:{node.get('id', '')} requires current material-gate evidence")
        entitlement_errors.extend(decision["entitlement_errors"])
        material_errors.extend(decision["material_errors"])
        irreversible = [
            {"index": index, "operation": item.get("op"), "reason": "node removal"}
            for index, item in enumerate(operations) if item.get("op") in {"remove_node", "disconnect"}
        ]
        errors = schema_errors + entitlement_errors + material_errors
        gate_input = {
            "base_draft_hash": base_draft_hash, "operations": operations,
            # Snapshot rows are append-only; schema hash, rather than a newly
            # minted row id, is the semantic registry version for confirmation.
            "registry_schema_hash": registry.schema_hash,
            "compiled_plan_hash": compiled_plan_hash, "estimated_cost": estimated_cost,
            "budget_limit": max_cost, "entitlement_errors": entitlement_errors,
            "material_errors": material_errors,
        }
        return {
            "state": "valid" if not errors else "invalid",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "registry_snapshot_id": str(registry.snapshot_id),
            "compiled_plan_hash": compiled_plan_hash or None,
            "validated_plan_hash": _hash(gate_input),
            "schema_errors": schema_errors,
            "entitlement_errors": entitlement_errors,
            "material_gate_errors": material_errors,
            "policy_decision": {"decision_id": decision["decision_id"], "decision_hash": decision["decision_hash"], "policy_revision": decision["policy_revision"]},
            "cost_estimate": {"amount": estimated_cost, "currency": "credits", "budget_limit": max_cost},
            "irreversible_impacts": irreversible,
            "non_blocking_diagnostics": (["No active registry definitions are installed; proposal cannot be published until a registry is configured."] if not node_defs else []),
        }

    def _append(self, proposal_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        with self._factory.begin() as s:
            s.add(
                ArtifactVersionModel(
                    artifact_version_id=uuid4(),
                    artifact_id=proposal_id,
                    schema_id=_SCHEMA,
                    schema_version=1,
                    owner_scope=payload["owner_scope"],
                    content_json=payload,
                    content_hash=_hash(payload),
                    metadata_json={"proposal_state": payload["state"]},
                    created_at=datetime.now(timezone.utc),
                )
            )
        return payload

    @staticmethod
    def _validate_operations(operations: list[dict[str, Any]]) -> None:
        allowed = {
            "add_node",
            "remove_node",
            "update_node",
            "connect",
            "disconnect",
            "layout_hint",
        }
        if not operations or len(operations) > 50:
            raise ValidationError_("Architect proposal must contain 1-50 operations")
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or operation.get("op") not in allowed:
                raise ValidationError_(
                    "Unknown Architect graph operation",
                    details={"field": f"operations[{index}]"},
                )
            raw = json.dumps(operation, ensure_ascii=True).lower()
            if "latest" in raw or any(
                word in raw
                for word in (
                    "<script",
                    "resource_commit",
                    "workbench_task",
                    "agent_invoke",
                    "http://",
                    "https://",
                )
            ):
                raise ValidationError_(
                    "Architect proposal contains forbidden implicit capability",
                    details={"field": f"operations[{index}]"},
                )

    @staticmethod
    def _apply_operations(
        graph: dict[str, Any], operations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        nodes = list(graph.get("nodes", []))
        edges = list(graph.get("edges", []))
        node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict)}
        for operation in operations:
            kind = operation["op"]
            if kind == "add_node":
                node = operation.get("node")
                if (
                    not isinstance(node, dict)
                    or not node.get("id")
                    or not node.get("type")
                    or str(node["id"]) in node_ids
                ):
                    raise ValidationError_("Invalid add_node operation")
                nodes.append(node)
                node_ids.add(str(node["id"]))
            elif kind == "remove_node":
                node_id = str(operation.get("node_id", ""))
                nodes = [node for node in nodes if str(node.get("id")) != node_id]
                edges = [
                    edge
                    for edge in edges
                    if str(edge.get("source")) != node_id
                    and str(edge.get("target")) != node_id
                ]
            elif kind == "update_node":
                node_id = str(operation.get("node_id", ""))
                patch = operation.get("patch", {})
                target = next(
                    (node for node in nodes if str(node.get("id")) == node_id), None
                )
                if target is None or not isinstance(patch, dict):
                    raise ValidationError_("Invalid update_node operation")
                target.update(patch)
            elif kind == "connect":
                edge = operation.get("edge")
                if (
                    not isinstance(edge, dict)
                    or str(edge.get("source")) not in node_ids
                    or str(edge.get("target")) not in node_ids
                ):
                    raise ValidationError_("Invalid connect operation")
                edges.append(edge)
            elif kind == "disconnect":
                edge_id = str(operation.get("edge_id", ""))
                edges = [edge for edge in edges if str(edge.get("id")) != edge_id]
        return {**graph, "nodes": nodes, "edges": edges}
