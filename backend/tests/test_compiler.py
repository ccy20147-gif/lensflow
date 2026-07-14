"""
ToonFlow Backend — Compiler Tests (TF-WF-003)
"""
from __future__ import annotations

import uuid
import pytest

from src.domain.workflow.compiler import WorkflowCompiler, CompilationError
from src.schemas.models import RegistrySnapshot, NodeDefinitionRevision, PortTypeRef


@pytest.fixture
def compiler():
    return WorkflowCompiler()


@pytest.fixture
def registry():
    snapshot = RegistrySnapshot(
        snapshot_id=uuid.uuid4(),
        node_definitions={
            "brief": NodeDefinitionRevision(
                node_type_id="brief",
                revision_id=uuid.uuid4(),
                semantic_version="1.0.0",
                input_ports=[
                    PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required"),
                ],
                output_ports=[
                    PortTypeRef(port_id="out", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required"),
                ],
                config_schema={"type": "object"},
            ),
            "generate": NodeDefinitionRevision(
                node_type_id="generate",
                revision_id=uuid.uuid4(),
                semantic_version="1.0.0",
                input_ports=[
                    PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required"),
                ],
                output_ports=[
                    PortTypeRef(port_id="out", type_id="artifact", schema_id="generated", schema_version=1, cardinality="required"),
                ],
            ),
        },
    )
    # Runtime primitives are no longer compiler fallbacks. Every node in a
    # test plan must be represented by its fixed registry snapshot too.
    for type_id in ("agent_invoke", "human_gate", "workbench_task", "resource_commit", "subworkflow_call"):
        snapshot.node_definitions[type_id] = NodeDefinitionRevision(
            node_type_id=type_id,
            revision_id=uuid.uuid4(),
            semantic_version="1.0.0",
            executor_ref=f"workflow.{type_id}",
        )
    return snapshot


class TestCompiler:
    """FR-1 to FR-12 from TF-WF-003"""

    def test_valid_graph_compiles(self, compiler, registry):
        """FR-1: Any run launch requires CompiledExecutionPlan"""
        graph = {
            "nodes": [
                {"id": "n1", "type": "brief"},
                {"id": "n2", "type": "generate"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "in"},
            ],
        }
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph=graph,
            registry_snapshot=registry,
        )
        assert plan.plan_id is not None
        assert plan.plan_hash != ""

    def test_empty_graph_rejected(self, compiler, registry):
        """FR-3: Must reject illegal graph"""
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [], "edges": []},
                registry_snapshot=registry,
            )
        # Error is stored in details.diagnostics
        assert "不包含" in str(exc.value.details)

    def test_unknown_node_type_rejected(self, compiler, registry):
        """FR-3: Unknown node type rejected"""
        graph = {
            "nodes": [{"id": "n1", "type": "nonexistent"}],
            "edges": [],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph=graph,
                registry_snapshot=registry,
            )
        assert "未注册" in str(exc.value.details)

    def test_cycle_detected(self, compiler, registry):
        """FR-3: Cycle detection"""
        graph = {
            "nodes": [
                {"id": "n1", "type": "brief"},
                {"id": "n2", "type": "generate"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "in"},
                {"source": "n2", "target": "n1", "sourceHandle": "out", "targetHandle": "in"},
            ],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph=graph,
                registry_snapshot=registry,
            )
        assert "循环" in str(exc.value.details)

    def test_dangling_edge_rejected(self, compiler, registry):
        """FR-3: Edge referencing missing node"""
        graph = {
            "nodes": [{"id": "n1", "type": "brief"}],
            "edges": [{"source": "n1", "target": "missing_node", "sourceHandle": "out", "targetHandle": "in"}],
        }
        with pytest.raises(CompilationError):
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph=graph,
                registry_snapshot=registry,
            )

    def test_plan_hash_stable(self, compiler, registry):
        """FR-11: Plan hash unaffected by layout changes"""
        graph1 = {
            "nodes": [{"id": "n1", "type": "brief"}],
            "edges": [],
        }
        graph2 = {
            "nodes": [{"id": "n1", "type": "brief"}],
            "edges": [],
        }
        plan1 = compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph=graph1,
            registry_snapshot=registry,
        )
        plan2 = compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph=graph2,
            registry_snapshot=registry,
        )
        # Different workflow_revision_id should produce different hashes
        assert plan1.plan_hash != plan2.plan_hash

    def test_plan_hash_verification(self, compiler, registry):
        """Verify plan hash round-trip"""
        graph = {
            "nodes": [{"id": "n1", "type": "brief"}],
            "edges": [],
        }
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph=graph,
            registry_snapshot=registry,
        )
        assert compiler.validate_plan_hash(plan) is True

    def test_dry_run_never_raises(self, compiler, registry):
        """FR-10 (partial): dry_run returns structured diagnostics, never raises"""
        passes, diagnostics = compiler.dry_run(
            graph={"nodes": [], "edges": []},
            registry_snapshot=registry,
        )
        assert passes is False
        assert len(diagnostics) > 0

    def test_port_compatibility(self, compiler):
        """FR-2: Port type compatibility"""
        source = PortTypeRef(port_id="out", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")
        target = PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")

        ok, msg = compiler.detect_port_compatibility(source, target)
        assert ok is True

        # Mismatched type
        bad_target = PortTypeRef(port_id="in", type_id="resource", schema_id="world", schema_version=1, cardinality="required")
        ok, msg = compiler.detect_port_compatibility(source, bad_target)
        assert ok is False

    def test_managed_agent_card_materializes_workflow_owned_tasks(self, compiler, registry):
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph={
                "nodes": [{
                    "id": "managed", "type": "brief",
                    "data": {"managed_task_plan": [
                        {"kind": "agent_invoke", "agent_revision_id": str(uuid.uuid4())},
                        {"kind": "workbench_task"},
                        {"kind": "resource_commit"},
                    ]},
                }],
                "edges": [],
            },
            registry_snapshot=registry,
        )
        nodes = plan.resolved_graph["nodes"]
        assert [node["type"] for node in nodes] == ["agent_invoke", "workbench_task", "resource_commit"]
        assert all(node["data"]["owner_layer"] == "workflow" for node in nodes)
        assert len(plan.resolved_graph["edges"]) == 2

    def test_managed_agent_card_rejects_unpinned_agent(self, compiler, registry):
        with pytest.raises(CompilationError):
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [{"id": "managed", "type": "brief", "data": {"managed_task_plan": [{"kind": "agent_invoke"}]}}], "edges": []},
                registry_snapshot=registry,
            )

    def test_subworkflow_rejects_untyped_or_latest_port_mapping(self, compiler, registry):
        child = str(uuid.uuid4())
        base = {
            "workflow_revision_id": child, "depth": 1, "max_depth": 2, "max_child_nodes": 10,
            "input_mapping": {"brief": {"source_port": "out", "target_port": "in", "schema_id": "brief", "schema_version": 1}},
            "output_mapping": {"result": {"source_port": "out", "target_port": "in", "schema_id": "result", "schema_version": 1}},
        }
        plan = compiler.compile(workflow_revision_id=uuid.uuid4(), graph={"nodes": [{"id": "call", "type": "subworkflow_call", "config": base}], "edges": []}, registry_snapshot=registry)
        assert plan.resolved_graph["nodes"][0]["id"] == "call"
        invalid = {**base, "input_mapping": {"brief": {"source_port": "latest", "target_port": "in", "schema_id": "brief", "schema_version": 1}}}
        with pytest.raises(CompilationError):
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph={"nodes": [{"id": "call", "type": "subworkflow_call", "config": invalid}], "edges": []}, registry_snapshot=registry)

    @pytest.mark.parametrize("schema_type", ["integer", "number"])
    def test_config_rejects_bool_for_numeric_schema(self, compiler, registry, schema_type):
        registry.node_definitions["brief"].config_schema = {
            "type": "object", "properties": {"limit": {"type": schema_type}},
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [{"id": "n1", "type": "brief", "config": {"limit": True}}], "edges": []},
                registry_snapshot=registry,
            )
        assert "配置必须是" in str(exc.value.details)

    def test_config_rejects_unknown_and_nested_invalid_properties(self, compiler, registry):
        registry.node_definitions["brief"].config_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "options": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["retries"],
                    "properties": {"retries": {"type": "integer"}},
                },
            },
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [{"id": "n1", "type": "brief", "config": {"unknown": 1, "options": {"retries": True, "extra": 1}}}], "edges": []},
                registry_snapshot=registry,
            )
        diagnostics = exc.value.details["diagnostics"]
        assert {entry["location"] for entry in diagnostics if entry["severity"] == "error"} >= {
            "node:n1:config.unknown", "node:n1:config.options.retries", "node:n1:config.options.extra",
        }

    def test_persisted_definition_revision_must_match_frozen_registry(self, compiler, registry):
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [{"id": "n1", "type": "brief", "data": {"definition_revision_id": str(uuid.uuid4())}}], "edges": []},
                registry_snapshot=registry,
            )
        assert "冻结 RegistrySnapshot" in str(exc.value.details)
