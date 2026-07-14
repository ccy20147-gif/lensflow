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

from src.core.exceptions import SafeError
from src.schemas.models import (
    CompiledExecutionPlan,
    PortTypeRef,
    RegistrySnapshot,
    ResourceRef,
    ArtifactRef,
)
from src.domain.workflow.node_definition import are_ports_compatible


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

    @staticmethod
    def partial_closure(*, graph: dict[str, Any], selected_node_ids: list[str], mode: str) -> dict[str, list[str]]:
        """Return the deterministic immutable closure used by a run slice.

        ``reuse`` is deliberately not a synonym for ``skip``.  It is the
        exact upstream boundary whose already-published outputs must be
        pinned into a slice input snapshot.  Everything else is skipped and
        must not be consulted by the slice scheduler.
        """
        if mode not in {"selected", "upstream", "downstream", "full"} or not selected_node_ids:
            raise ValueError("partial run requires a mode and selected nodes")
        nodes = {str(node.get("id", "")) for node in graph.get("nodes", []) if isinstance(node, dict)}
        selected = set(selected_node_ids)
        if not selected <= nodes:
            raise ValueError("partial run references an unknown node")
        edges = [(str(edge.get("source", "")), str(edge.get("target", ""))) for edge in graph.get("edges", []) if isinstance(edge, dict)]
        def walk(seed: set[str], *, reverse: bool) -> set[str]:
            result = set(seed)
            changed = True
            while changed:
                changed = False
                for source, target in edges:
                    candidate = source if reverse and target in result else target if not reverse and source in result else None
                    if candidate is not None and candidate not in result:
                        result.add(candidate)
                        changed = True
            return result

        upstream = walk(selected, reverse=True)
        downstream = walk(selected, reverse=False)
        if mode == "selected":
            execute, reuse = selected, upstream - selected
        elif mode == "upstream":
            execute, reuse = upstream, set()
        elif mode == "downstream":
            execute, reuse = downstream, upstream - selected
        else:  # full: its semantic is the whole fixed revision, not a preview.
            execute, reuse = nodes, set()
        return {
            "execute": sorted(execute),
            "reuse": sorted(reuse),
            "skip": sorted(nodes - execute - reuse),
        }

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
        try:
            nodes, edges = self._materialize_managed_agent_task_plans(nodes, edges)
        except ValueError as exc:
            diagnostics.append({"severity": "error", "location": "managed_agent", "message": str(exc)})

        # 1. Validate basic structure
        if not nodes:
            diagnostics.append({
                "severity": "error",
                "location": "graph",
                "message": "工作流图不包含任何节点",
            })

        budget_limits = budget_limits or {}
        # 2. Validate node references, frozen config and policy metadata.
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
                continue
            node_data = node.get("data") if isinstance(node.get("data"), dict) else {}
            declared_revision = node_data.get("definition_revision_id", node.get("definition_revision_id"))
            if declared_revision is not None and str(declared_revision) != str(defn.revision_id):
                diagnostics.append({
                    "severity": "error",
                    "location": f"node:{node_id}:definition_revision_id",
                    "message": "节点定义版本与冻结 RegistrySnapshot 不一致",
                })
            diagnostics.extend(self._validate_config(node, defn))
            diagnostics.extend(self._validate_control_node(node, workflow_revision_id))

        # 3. Validate edge connections
        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            edge.get("sourceHandle", "")
            edge.get("targetHandle", "")

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

        # 4b. Validate port compatibility for each edge
        port_diag, used_converters = self._validate_port_types(nodes, edges, registry_snapshot)
        diagnostics.extend(port_diag)
        diagnostics.extend(self._validate_required_ports(nodes, edges, registry_snapshot))
        diagnostics.extend(self._validate_budget_and_capabilities(nodes, registry_snapshot, budget_limits))
        diagnostics.extend(self._validate_fixed_refs(resolved_input_refs or []))

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
        executor_refs = {
            nid: registry_snapshot.node_definitions[n["type"]].executor_ref
            for nid, n in resolved_nodes.items()
        }
        policy_revisions = sorted({
            str(definition.policy_metadata.get("policy_revision", "platform.default.v1"))
            for definition in registry_snapshot.node_definitions.values()
        })
        capability_snapshots = sorted({
            str(capability)
            for definition in registry_snapshot.node_definitions.values()
            for capability in definition.policy_metadata.get("provider_capabilities", [])
            if isinstance(capability, str)
        })
        provider_policy_ref = str(budget_limits.get("provider_selection_policy_ref", "atlascloud.default.v1"))
        plan_hash_input = json.dumps({
            "workflow_revision_id": str(workflow_revision_id),
            "nodes": sorted(
                json.dumps({k: v for k, v in n.items() if k not in ("position",)}, sort_keys=True)
                for n in nodes
            ),
            "edges": sorted(self._semantic_edge(edge) for edge in edges),
            "registry_snapshot_id": str(registry_snapshot.snapshot_id),
            "converters": used_converters,
            "executor_refs": executor_refs,
            "provider_policy_ref": provider_policy_ref,
            "capability_snapshots": capability_snapshots,
            "policy_revisions": policy_revisions,
            "resolved_input_refs": [ref.model_dump(mode="json") for ref in (resolved_input_refs or [])],
            "budget_limits": budget_limits,
        }, sort_keys=True)
        plan_hash = hashlib.sha256(plan_hash_input.encode()).hexdigest()[:16]

        return CompiledExecutionPlan(
            plan_id=plan_id,
            workflow_revision_id=workflow_revision_id,
            registry_snapshot=registry_snapshot,
            resolved_graph={**graph, "nodes": nodes, "edges": edges},
            definition_snapshots={nid: registry_snapshot.node_definitions[n["type"]] for nid, n in resolved_nodes.items()},
            converter_revisions=used_converters,
            resolved_input_refs=resolved_input_refs or [],
            executor_refs=executor_refs,
            provider_policy_ref=provider_policy_ref,
            capability_snapshots=capability_snapshots,
            policy_revisions=policy_revisions,
            budget_limits=budget_limits,
            compiler_version=self.compiler_version,
            plan_hash=plan_hash,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _validate_control_node(node: dict[str, Any], workflow_revision_id: uuid.UUID) -> list[dict[str, Any]]:
        """Freeze bounded control-flow policy before a run is created."""
        node_type = str(node.get("type", ""))
        if node_type not in {"map", "ordered_map", "fold", "subworkflow_call", "condition", "join", "fallback"}:
            return []
        node_id = str(node.get("id", ""))
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        cfg = node.get("config") if isinstance(node.get("config"), dict) else data.get("config", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        errors: list[dict[str, Any]] = []
        def error(message: str) -> None:
            errors.append({"severity": "error", "location": f"node:{node_id}", "message": message})
        if node_type in {"map", "ordered_map", "fold"}:
            try:
                max_items = int(cfg.get("max_items", 0))
                max_concurrency = int(cfg.get("max_concurrency", 0))
            except (TypeError, ValueError):
                error("Map bounds must be integers")
                return errors
            if not 1 <= max_items <= 10_000:
                error("Map requires bounded max_items between 1 and 10000")
            if not 1 <= max_concurrency <= max_items:
                error("Map max_concurrency must be within max_items")
            if node_type in {"ordered_map", "fold"} and max_concurrency != 1:
                error("OrderedMap/Fold requires max_concurrency=1")
            if cfg.get("failure_policy", "fail_fast") not in {"fail_fast", "collect_errors", "configured_fallback"}:
                error("Map failure_policy is invalid")
            if cfg.get("failure_policy") == "configured_fallback" and not cfg.get("fallback_node_id"):
                error("configured_fallback requires fallback_node_id")
        elif node_type == "subworkflow_call":
            revision = cfg.get("workflow_revision_id")
            try:
                child = uuid.UUID(str(revision))
                if child == workflow_revision_id:
                    error("SubworkflowCall cannot recursively reference its own revision")
            except (TypeError, ValueError):
                error("SubworkflowCall requires a fixed workflow_revision_id")
            try:
                depth, max_depth = int(cfg.get("depth", 1)), int(cfg.get("max_depth", 0))
                max_nodes = int(cfg.get("max_child_nodes", 0))
            except (TypeError, ValueError):
                error("Subworkflow bounds must be integers")
                return errors
            if not 1 <= depth <= max_depth <= 16:
                error("Subworkflow depth must be bounded between 1 and 16")
            if not 1 <= max_nodes <= 10_000:
                error("Subworkflow max_child_nodes must be bounded")
            if not isinstance(cfg.get("input_mapping"), dict) or not isinstance(cfg.get("output_mapping"), dict):
                error("SubworkflowCall requires typed input_mapping and output_mapping")
            for mapping_name in ("input_mapping", "output_mapping"):
                mapping = cfg.get(mapping_name, {})
                if not isinstance(mapping, dict):
                    continue
                for port_name, binding in mapping.items():
                    # Empty mappings are valid for a no-port child.  Once a
                    # port is mapped it is a typed, frozen contract rather
                    # than an arbitrary JSON pointer or a latest reference.
                    if not isinstance(binding, dict):
                        error(f"Subworkflow {mapping_name}.{port_name} must be a typed mapping object")
                        continue
                    if any(str(value).lower() == "latest" for value in binding.values()):
                        error(f"Subworkflow {mapping_name}.{port_name} cannot reference latest")
                    if not {"source_port", "target_port", "schema_id", "schema_version"} <= set(binding):
                        error(f"Subworkflow {mapping_name}.{port_name} requires source_port, target_port, schema_id and schema_version")
                    elif not isinstance(binding.get("schema_version"), int) or int(binding["schema_version"]) < 1:
                        error(f"Subworkflow {mapping_name}.{port_name} schema_version must be positive")
        elif node_type == "join" and cfg.get("strategy") not in {"any", "all", "merge"}:
            error("Join requires explicit any, all, or merge strategy")
        elif node_type == "fallback" and not cfg.get("error_categories"):
            error("Fallback requires consumable error_categories")
        elif node_type == "condition" and not cfg.get("default_branch"):
            error("Condition requires an explicit default_branch")
        return errors

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
        nodes_list = plan.resolved_graph.get("nodes", [])
        edges_list = plan.resolved_graph.get("edges", [])

        def _node_key(n):
            """Serialize a node dict to a stable string key."""
            if isinstance(n, dict):
                return json.dumps({k: v for k, v in n.items() if k not in ("position",)}, sort_keys=True)
            return str(n)

        plan_hash_input = json.dumps({
            "workflow_revision_id": str(plan.workflow_revision_id),
            "nodes": sorted(_node_key(n) for n in nodes_list),
            "edges": sorted(self._semantic_edge(e) for e in edges_list),
            "registry_snapshot_id": str(plan.registry_snapshot.snapshot_id),
            "converters": plan.converter_revisions,
            "executor_refs": plan.executor_refs,
            "provider_policy_ref": plan.provider_policy_ref,
            "capability_snapshots": plan.capability_snapshots,
            "policy_revisions": plan.policy_revisions,
            "resolved_input_refs": [ref.model_dump(mode="json") for ref in plan.resolved_input_refs],
            "budget_limits": plan.budget_limits,
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

    @staticmethod
    def _materialize_managed_agent_task_plans(
        nodes: list[dict], edges: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Expand a managed Agent presentation card into workflow-owned tasks.

        The plan is accepted only on a Workflow node. It is never read from an
        AgentRevision, which keeps Agent execution unable to manufacture human
        tasks or ResourceCommit. The returned graph is the immutable advanced
        execution view used by the runtime and trace UI.
        """
        expanded_nodes: list[dict] = []
        replacement: dict[str, tuple[str, str]] = {}
        allowed = {"agent_invoke", "request_input", "human_gate", "workbench_task", "resource_commit"}
        for node in nodes:
            data = node.get("data", {}) if isinstance(node, dict) else {}
            task_plan = data.get("managed_task_plan") if isinstance(data, dict) else None
            if task_plan is None:
                expanded_nodes.append(node)
                continue
            node_id = str(node.get("id", ""))
            if not node_id or not isinstance(task_plan, list) or not task_plan:
                raise ValueError("managed Agent node requires a non-empty managed_task_plan")
            generated: list[dict] = []
            for index, task in enumerate(task_plan):
                if not isinstance(task, dict) or task.get("kind") not in allowed:
                    raise ValueError(f"managed Agent task {node_id}[{index}] has an unsupported kind")
                kind = str(task["kind"])
                if kind == "agent_invoke" and not task.get("agent_revision_id"):
                    raise ValueError(f"managed Agent task {node_id}[{index}] requires pinned agent_revision_id")
                generated.append({
                    "id": f"{node_id}:task:{index}", "type": kind,
                    "data": {"owner_layer": "workflow", "managed_card_id": node_id, **task},
                })
            replacement[node_id] = (generated[0]["id"], generated[-1]["id"])
            expanded_nodes.extend(generated)
        expanded_edges: list[dict] = []
        for edge in edges:
            copied = dict(edge)
            source = str(copied.get("source", ""))
            target = str(copied.get("target", ""))
            if source in replacement:
                copied["source"] = replacement[source][1]
            if target in replacement:
                copied["target"] = replacement[target][0]
            expanded_edges.append(copied)
        for first, last in replacement.values():
            start = next(index for index, node in enumerate(expanded_nodes) if node["id"] == first)
            finish = next(index for index, node in enumerate(expanded_nodes) if node["id"] == last)
            for index in range(start, finish):
                expanded_edges.append({"id": f"{expanded_nodes[index]['id']}->managed", "source": expanded_nodes[index]["id"], "target": expanded_nodes[index + 1]["id"]})
        return expanded_nodes, expanded_edges

    @staticmethod
    def _semantic_edge(edge: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(edge.get("source", "")), str(edge.get("target", "")),
            str(edge.get("sourceHandle", edge.get("source_handle", ""))),
            str(edge.get("targetHandle", edge.get("target_handle", ""))),
            str(edge.get("kind", edge.get("edge_type", "data"))),
        )

    def _validate_port_types(self, nodes: list[dict], edges: list[dict], registry_snapshot: RegistrySnapshot) -> tuple[list[dict], dict[str, str]]:
        """Validate port type compatibility across all edges."""
        diag: list[dict] = []
        used_converters: dict[str, str] = {}
        converters: set[tuple[str, str, int]] = set()
        converter_by_key: dict[tuple[str, str, int], tuple[str, str]] = {}
        for raw_key, revision in registry_snapshot.converter_revisions.items():
            try:
                source, destination = raw_key.split("→", 1)
                target, version = destination.rsplit("@v", 1)
                key = (source, target, int(version))
                converters.add(key)
                converter_by_key[key] = (raw_key, revision)
            except (ValueError, TypeError):
                continue
        node_map = {n.get("id", ""): n for n in nodes}
        for e in edges:
            if str(e.get("id", "")).endswith("->managed"):
                # Materialised managed-card sequencing is compiler-owned;
                # ports are represented by the surrounding card contract.
                continue
            src = e.get("source", "")
            tgt = e.get("target", "")
            sp = e.get("sourceHandle", "")
            tp = e.get("targetHandle", "")
            src_node = node_map.get(src, {})
            tgt_node = node_map.get(tgt, {})
            src_type = src_node.get("type", "")
            tgt_type = tgt_node.get("type", "")

            src_def = registry_snapshot.node_definitions.get(src_type)
            tgt_def = registry_snapshot.node_definitions.get(tgt_type)

            if src_def and tgt_def:
                src_port = next((p for p in src_def.output_ports if p.port_id == sp), None)
                tgt_port = next((p for p in tgt_def.input_ports if p.port_id == tp), None)
                if src_port and tgt_port:
                    compatible = are_ports_compatible(src_port, tgt_port, converters)
                    if not compatible:
                        diag.append({
                            "severity": "error",
                            "location": f"edge:{src}.{sp}->{tgt}.{tp}",
                            "message": f"端口类型不兼容: {src_type}.{sp} -> {tgt_type}.{tp}",
                        })
                    elif src_port.schema_id != tgt_port.schema_id:
                        key = (src_port.schema_id, tgt_port.schema_id, tgt_port.schema_version)
                        converter = converter_by_key.get(key)
                        if converter is None:
                            diag.append({"severity": "error", "location": f"edge:{src}.{sp}->{tgt}.{tp}", "message": "显式转换器缺失"})
                        else:
                            used_converters[converter[0]] = converter[1]
                else:
                    diag.append({
                        "severity": "error",
                        "location": f"edge:{src}.{sp}->{tgt}.{tp}",
                        "message": f"端口 '{sp}'/'{tp}' 未在节点定义中找到",
                    })
        return diag, used_converters

    @staticmethod
    def _node_config(node: dict[str, Any]) -> dict[str, Any]:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        value = node.get("config") if isinstance(node.get("config"), dict) else data.get("config", {})
        return value if isinstance(value, dict) else {}

    def _validate_config(self, node: dict[str, Any], definition: Any) -> list[dict]:
        schema = definition.config_schema or {}
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        raw_config = node.get("config", data.get("config", {}))
        config = raw_config if isinstance(raw_config, dict) else {}
        node_id = str(node.get("id", ""))
        diagnostics: list[dict] = []
        if schema and schema.get("type") == "object" and not isinstance(raw_config, dict):
            return [{"severity": "error", "location": f"node:{node_id}:config", "message": "节点配置必须是对象"}]
        if not isinstance(schema, dict):
            return diagnostics

        def validate(value: Any, rule: dict[str, Any], path: str) -> None:
            expected = rule.get("type")
            valid = True
            if expected == "string":
                valid = isinstance(value, str)
            elif expected == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif expected == "number":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif expected == "boolean":
                valid = isinstance(value, bool)
            elif expected == "object":
                valid = isinstance(value, dict)
            elif expected == "array":
                valid = isinstance(value, list)
            if not valid:
                diagnostics.append({"severity": "error", "location": path, "message": f"配置必须是 {expected}"})
                return
            if "enum" in rule and value not in rule["enum"]:
                diagnostics.append({"severity": "error", "location": path, "message": "配置不在允许枚举中"})
            if isinstance(value, dict):
                properties = rule.get("properties", {})
                if not isinstance(properties, dict):
                    properties = {}
                for required in rule.get("required", []):
                    if required not in value:
                        diagnostics.append({"severity": "error", "location": f"{path}.{required}", "message": "缺少必需配置"})
                if rule.get("additionalProperties") is False:
                    for key in value:
                        if key not in properties:
                            diagnostics.append({"severity": "error", "location": f"{path}.{key}", "message": "不允许未知配置"})
                for key, child in properties.items():
                    if key in value and isinstance(child, dict):
                        validate(value[key], child, f"{path}.{key}")

        validate(config, schema, f"node:{node_id}:config")
        return diagnostics

    def _validate_required_ports(self, nodes: list[dict], edges: list[dict], registry_snapshot: RegistrySnapshot) -> list[dict]:
        incoming = {(str(edge.get("target", "")), str(edge.get("targetHandle", edge.get("target_handle", "")))) for edge in edges}
        nodes_with_predecessor = {node_id for node_id, _port in incoming}
        diagnostics: list[dict] = []
        for node in nodes:
            definition = registry_snapshot.node_definitions.get(str(node.get("type", "")))
            if definition is None:
                continue
            for port in definition.input_ports:
                # Root inputs are supplied by the fixed run-input snapshot;
                # graph edges only satisfy non-root required ports.
                if str(node.get("id", "")) not in nodes_with_predecessor:
                    continue
                if port.cardinality == "required" and (str(node.get("id", "")), port.port_id) not in incoming:
                    diagnostics.append({"severity": "error", "location": f"node:{node.get('id')}:port:{port.port_id}", "message": "缺少必需输入端口"})
        return diagnostics

    def _validate_budget_and_capabilities(self, nodes: list[dict], registry_snapshot: RegistrySnapshot, budget: dict[str, Any]) -> list[dict]:
        diagnostics: list[dict] = []
        available = set(budget.get("available_capabilities", []))
        total = 0.0
        for node in nodes:
            definition = registry_snapshot.node_definitions.get(str(node.get("type", "")))
            if definition is None:
                continue
            metadata = definition.policy_metadata or {}
            required = set(metadata.get("provider_capabilities", []))
            if required and not required <= available:
                diagnostics.append({"severity": "error", "location": f"node:{node.get('id')}", "message": f"Provider capability 缺失: {', '.join(sorted(required - available))}"})
            estimate = metadata.get("cost_estimate", 0)
            if isinstance(estimate, (int, float)):
                total += float(estimate)
        maximum = budget.get("max_cost")
        if isinstance(maximum, (int, float)) and total > float(maximum):
            diagnostics.append({"severity": "error", "location": "budget", "message": "预算上限不足"})
        return diagnostics

    @staticmethod
    def _validate_fixed_refs(refs: list[ResourceRef | ArtifactRef]) -> list[dict]:
        diagnostics: list[dict] = []
        for index, ref in enumerate(refs):
            if isinstance(ref, ResourceRef) and not ref.revision_id:
                diagnostics.append({"severity": "error", "location": f"input:{index}", "message": "ResourceRef 必须固定 revision"})
            if isinstance(ref, ArtifactRef) and not ref.artifact_version_id:
                diagnostics.append({"severity": "error", "location": f"input:{index}", "message": "ArtifactRef 必须固定 version"})
        return diagnostics

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
