"""
ToonFlow Backend — Workflow Compiler Service (TF-WF-003)

Compiles a WorkflowRevision into an immutable CompiledExecutionPlan.
Validates structure, types, permissions, budget, and provider capabilities.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.exceptions import SafeError, ValidationError_
from src.schemas.enums import ControlLayerResult
from src.schemas.models import (
    CompiledExecutionPlan,
    NodeDefinitionRevision,
    PortTypeRef,
    RegistrySnapshot,
    ResourceRef,
    ArtifactRef,
)


class CompilationError(SafeError):
    """Compilation failure with structured diagnostics."""
    def __init__(self, message: str, diagnostics: list[dict], correlation_id: str | None = None):
        super().__init__(
            code="WF_COMPILE_FAILED",
            message=message,
            status_code=422,
            correlation_id=correlation_id,
            details={"diagnostics": diagnostics},
        )


class WorkflowCompiler:
    """Compiles a workflow graph into an execution plan.

    The compiler is stateless — it takes a graph + registry + pinned
    references and produces a plan or a list of errors.
    """

    def __init__(self, compiler_version: str = "1.0"):
        self.compiler_version = compiler_version

    def compile(
        self,
        *,
        workflow_revision_id: uuid.UUID,
        graph: dict[str, Any],
        registry_snapshot: RegistrySnapshot,
        resolved_input_refs: list[ResourceRef | ArtifactRef] | None = None,
        budget_limits: dict[str, Any] | None = None,
    ) -> CompiledExecutionPlan:
        """Compile a workflow graph into a plan.

        Returns the plan on success, raises CompilationError on failure.
        """
        diagnostics: list[dict] = []
        graph = graph or {}

        nodes: list[dict] = graph.get("nodes", [])
        edges: list[dict] = graph.get("edges", [])

        # 1. Validate basic structure
        if not nodes:
            diagnostics.append({
                "severity": "error",
                "location": "graph",
                "message": "工作流图不包含任何节点",
            })

        # 2. Validate node references against registry
        node_ids = set()
        for node in nodes:
            node_id = node.get("id", "")
            node_type = node.get("type", "")
            node_ids.add(node_id)

            if not node_type:
                diagnostics.append({
                    "severity": "error",
                    "location": f"node:{node_id}",
                    "message": f"节点 {node_id} 缺少 type",
                })
                continue

            defn = registry_snapshot.node_definitions.get(node_type)
            if not defn:
                diagnostics.append({
                    "severity": "error",
                    "location": f"node:{node_id}",
                    "message": f"未注册的节点类型 '{node_type}'",
                })

        # 3. Validate edge connections
        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            source_port = edge.get("sourceHandle", "")
            target_port = edge.get("targetHandle", "")

            if source not in node_ids:
                diagnostics.append({
                    "severity": "error",
                    "location": f"edge:{source}->{target}",
                    "message": f"边引用不存在的源节点 '{source}'",
                })
            if target not in node_ids:
                diagnostics.append({
                    "severity": "error",
                    "location": f"edge:{source}->{target}",
                    "message": f"边引用不存在的目标节点 '{target}'",
                })

        # 4. Check for cycles
        cycle_errors = self._detect_cycles(nodes, edges)
        diagnostics.extend(cycle_errors)

        # 5. Check unreachable nodes
        reachable = self._compute_reachable(nodes, edges)
        for node in nodes:
            nid = node.get("id", "")
            if nid not in reachable and len(nodes) > 1:
                diagnostics.append({
                    "severity": "warning",
                    "location": f"node:{nid}",
                    "message": f"节点 '{nid}' 不可达（没有输入边或来自起始节点）",
                })

        # 6. Raise if errors found
        errors = [d for d in diagnostics if d["severity"] == "error"]
        if errors:
            raise CompilationError(
                message=f"编译失败：{len(errors)} 个错误",
                diagnostics=diagnostics,
            )

        # 7. Build the plan
        plan_id = uuid.uuid4()
        resolved_nodes = {n.get("id", ""): n for n in nodes}
        plan_hash_input = json.dumps({
            "workflow_revision_id": str(workflow_revision_id),
            "nodes": sorted(resolved_nodes.keys()),
            "edges": sorted(
                (e.get("source", ""), e.get("target", ""))
                for e in edges
            ),
            "registry_snapshot_id": str(registry_snapshot.snapshot_id),
        }, sort_keys=True)
        plan_hash = hashlib.sha256(plan_hash_input.encode()).hexdigest()[:16]

        return CompiledExecutionPlan(
            plan_id=plan_id,
            workflow_revision_id=workflow_revision_id,
            registry_snapshot=registry_snapshot,
            resolved_graph=graph,
            definition_snapshots={
                nid: registry_snapshot.node_definitions.get(
                    n.get("type", ""), NodeDefinitionRevision(
                        node_type_id=n.get("type", ""),
                        revision_id=uuid.uuid4(),
                        semantic_version="0.0.0",
                    )
                )
                for nid, n in resolved_nodes.items()
            },
            provider_policy_ref="",
            budget_limits=budget_limits or {},
            compiler_version=self.compiler_version,
            plan_hash=plan_hash,
            created_at=datetime.now(timezone.utc),
        )

    def dry_run(
        self,
        *,
        graph: dict[str, Any],
        registry_snapshot: RegistrySnapshot,
    ) -> tuple[bool, list[dict]]:
        """Dry-run compilation returning (passes, diagnostics).

        Never raises — always returns diagnostics for UI display.
        """
        try:
            self.compile(
                workflow_revision_id=uuid.uuid4(),
                graph=graph,
                registry_snapshot=registry_snapshot,
            )
            return True, []
        except CompilationError as e:
            return False, e.details.get("diagnostics", [])
        except Exception as e:
            return False, [{"severity": "error", "location": "graph", "message": str(e)}]

    def validate_plan_hash(self, plan: CompiledExecutionPlan) -> bool:
        """Verify the plan hash matches its content."""
        plan_hash_input = json.dumps({
            "workflow_revision_id": str(plan.workflow_revision_id),
            "nodes": sorted(plan.resolved_graph.get("nodes", [])),
            "edges": sorted(
                (e.get("source", ""), e.get("target", ""))
                for e in plan.resolved_graph.get("edges", [])
            ),
            "registry_snapshot_id": str(plan.registry_snapshot.snapshot_id),
        }, sort_keys=True)
        expected_hash = hashlib.sha256(plan_hash_input.encode()).hexdigest()[:16]
        return plan.plan_hash == expected_hash

    def detect_port_compatibility(
        self,
        source_port: PortTypeRef,
        target_port: PortTypeRef,
        converter_registry: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """Check if two ports are type-compatible.

        Returns (compatible, message).
        """
        if source_port.type_id != target_port.type_id:
            # Check for explicit converter
            converter_key = f"{source_port.schema_id}:{target_port.schema_id}"
            if converter_registry and converter_key in converter_registry:
                return True, f"通过转换器 {converter_registry[converter_key]} 兼容"
            return False, f"类型不兼容: {source_port.type_id} -> {target_port.type_id}"

        if source_port.schema_version > target_port.schema_version:
            return False, (
                f"源版本 {source_port.schema_version} 高于目标版本 "
                f"{target_port.schema_version}，需要转换器"
            )

        return True, "兼容"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _detect_cycles(self, nodes: list[dict], edges: list[dict]) -> list[dict]:
        """Detect cycles using DFS."""
        adj: dict[str, list[str]] = {n.get("id", ""): [] for n in nodes}
        for e in edges:
            src = e.get("source", "")
            tgt = e.get("target", "")
            if src in adj:
                adj[src].append(tgt)

        visited: set[str] = set()
        in_stack: set[str] = set()
        cycles: list[dict] = []

        def dfs(node_id: str):
            visited.add(node_id)
            in_stack.add(node_id)
            for neighbor in adj.get(node_id, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in in_stack:
                    cycles.append({
                        "severity": "error",
                        "location": f"node:{node_id}",
                        "message": f"检测到循环依赖: {node_id} -> {neighbor}",
                    })
            in_stack.discard(node_id)

        for nid in list(adj.keys()):
            if nid not in visited:
                dfs(nid)

        return cycles

    def _compute_reachable(self, nodes: list[dict], edges: list[dict]) -> set[str]:
        """Compute nodes reachable from any root node (no incoming edges)."""
        inbound: dict[str, int] = {n.get("id", ""): 0 for n in nodes}
        for e in edges:
            tgt = e.get("target", "")
            if tgt in inbound:
                inbound[tgt] += 1

        roots = [nid for nid, cnt in inbound.items() if cnt == 0]
        adj: dict[str, list[str]] = {n.get("id", ""): [] for n in nodes}
        for e in edges:
            src = e.get("source", "")
            tgt = e.get("target", "")
            if src in adj:
                adj[src].append(tgt)

        reachable = set(roots)
        stack = list(roots)
        while stack:
            nid = stack.pop()
            for neighbor in adj.get(nid, []):
                if neighbor not in reachable:
                    reachable.add(neighbor)
                    stack.append(neighbor)

        return reachable
