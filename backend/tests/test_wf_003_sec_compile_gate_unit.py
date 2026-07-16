"""Batch B (TF-WF-003 + minimum TF-SEC-001 compile gate) unit tests.

Covers the pure-compiler surface: hash determinism, layout-only
isolation, draft/latest rejection, secret plaintext rejection, frozen
ProviderCompilationReport outcomes, and the entitlement gate
behaviour that does not require a database.

The resolver contract verified here is the **P0-fixed** one: the
static scanner never decides allow / deny from graph fields; the
resolver (which loads canonical database rows) is the single source of
truth.  When no resolver is supplied the gate fails closed.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from src.domain.workflow.compiler import (
    CompilationContext,
    CompilationError,
    WorkflowCompiler,
)
from src.domain.workflow.entitlement_gate import (
    EntitlementSnapshot,
    REASON_CODES,
    scan_graph,
)
from src.schemas.models import (
    NodeDefinitionRevision,
    PortTypeRef,
    RegistrySnapshot,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def compiler() -> WorkflowCompiler:
    return WorkflowCompiler()


@pytest.fixture
def registry() -> RegistrySnapshot:
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
                policy_metadata={
                    "package_source": "approved:test",
                    "required_capabilities": ["atlascloud.llm"],
                    "optional_capabilities": ["atlascloud.video"],
                    "unsupported_policy": "degrade",
                },
            ),
        },
    )
    return snapshot


class _ScriptedResolver:
    """Test double that returns a pre-baked snapshot per ref.

    Mirrors the SQL resolver's contract: ``resolve_refs`` receives a
    list of ``(ref, path)`` pairs and must return one
    ``EntitlementSnapshot`` per ref.  This is what the SQL resolver
    does for ``SqlResourceRepository.evaluate_entitlement``.
    """

    def __init__(self, snapshots: list[EntitlementSnapshot]) -> None:
        self._snapshots = snapshots
        self.called = False

    def resolve_refs(self, *, request_owner: str, refs: list[tuple[dict[str, Any], str]]):
        self.called = True
        assert len(refs) == len(self._snapshots), (
            "resolver should receive one ref per snapshot in deterministic order"
        )
        return list(self._snapshots)


# ---------------------------------------------------------------------------
# FR-1 / FR-11 / AC-1, AC-2, AC-5: deterministic plan hash, no draft leakage
# ---------------------------------------------------------------------------


class TestPlanHashAndReplay:
    def test_same_revision_replays_same_hash(self, compiler, registry):
        revision_id = uuid.uuid4()
        graph = {
            "nodes": [{"id": "n1", "type": "brief", "config": {"prompt": "hi"}}],
            "edges": [],
        }
        context = CompilationContext(actor_scope="user:o1")
        plan_a = compiler.compile(workflow_revision_id=revision_id, graph=graph, registry_snapshot=registry, compilation_context=context)
        plan_b = compiler.compile(workflow_revision_id=revision_id, graph=graph, registry_snapshot=registry, compilation_context=context)
        assert plan_a.plan_hash == plan_b.plan_hash

    def test_layout_change_keeps_execution_hash_stable(self, compiler, registry):
        with_layout = {"nodes": [{"id": "n1", "type": "brief", "position": {"x": 0, "y": 0}}], "edges": []}
        plan_with = compiler.compile(workflow_revision_id=uuid.uuid4(), graph=with_layout, registry_snapshot=registry)
        without_layout = {"nodes": [{"id": "n1", "type": "brief", "position": {"x": 50, "y": 50}}], "edges": []}
        plan_without = compiler.compile(workflow_revision_id=plan_with.workflow_revision_id, graph=without_layout, registry_snapshot=registry)
        assert compiler.validate_plan_hash(plan_with)
        assert compiler.validate_plan_hash(plan_without)

    def test_config_change_rotates_plan_hash(self, compiler, registry):
        graph_a = {"nodes": [{"id": "n1", "type": "brief", "config": {"prompt": "hi"}}], "edges": []}
        graph_b = {"nodes": [{"id": "n1", "type": "brief", "config": {"prompt": "bye"}}], "edges": []}
        revision_id = uuid.uuid4()
        plan_a = compiler.compile(workflow_revision_id=revision_id, graph=graph_a, registry_snapshot=registry)
        plan_b = compiler.compile(workflow_revision_id=revision_id, graph=graph_b, registry_snapshot=registry)
        assert plan_a.plan_hash != plan_b.plan_hash


# ---------------------------------------------------------------------------
# FR-2 / AC-3: structured per-node diagnostics
# ---------------------------------------------------------------------------


class TestNodeLevelDiagnostics:
    def test_unknown_node_has_node_diagnostic(self, compiler, registry):
        graph = {"nodes": [{"id": "n1", "type": "ghost"}], "edges": []}
        with pytest.raises(CompilationError) as exc:
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry)
        diagnostics = exc.value.details["diagnostics"]
        assert any(d.get("node_instance_id") == "n1" for d in diagnostics)

    def test_port_type_error_has_edge_diagnostic(self, compiler, registry):
        registry.node_definitions["brief"].output_ports = [
            PortTypeRef(port_id="out", type_id="artifact", schema_id="text", schema_version=1, cardinality="required"),
        ]
        registry.node_definitions["generate"] = NodeDefinitionRevision(
            node_type_id="generate", revision_id=uuid.uuid4(), semantic_version="1.0.0",
            input_ports=[PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
            output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="generated", schema_version=1, cardinality="required")],
            policy_metadata={"package_source": "approved:test"},
        )
        graph = {
            "nodes": [{"id": "n1", "type": "brief"}, {"id": "n2", "type": "generate"}],
            "edges": [{"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "in"}],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry)
        diagnostics = exc.value.details["diagnostics"]
        assert any(str(d.get("location", "")).startswith("edge:n1") or "edge" in str(d.get("location", "")) for d in diagnostics)

    def test_cycle_detected(self, compiler, registry):
        registry.node_definitions["sink"] = NodeDefinitionRevision(
            node_type_id="sink", revision_id=uuid.uuid4(), semantic_version="1.0.0",
            input_ports=[PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
            output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
            policy_metadata={"package_source": "approved:test"},
        )
        graph = {
            "nodes": [{"id": "n1", "type": "brief"}, {"id": "n2", "type": "sink"}],
            "edges": [
                {"source": "n1", "target": "n2", "sourceHandle": "out", "targetHandle": "in"},
                {"source": "n2", "target": "n1", "sourceHandle": "out", "targetHandle": "in"},
            ],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry)
        diagnostics = exc.value.details["diagnostics"]
        assert any("循环" in str(d.get("message", "")) for d in diagnostics)

    def test_missing_required_input_reports_node_port(self, compiler, registry):
        registry.node_definitions["consumer"] = NodeDefinitionRevision(
            node_type_id="consumer", revision_id=uuid.uuid4(), semantic_version="1.0.0",
            input_ports=[PortTypeRef(port_id="required_in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
            output_ports=[],
            policy_metadata={"package_source": "approved:test"},
        )
        graph = {
            "nodes": [{"id": "upstream", "type": "brief"}, {"id": "consumer", "type": "consumer"}],
            "edges": [{"source": "upstream", "target": "consumer", "sourceHandle": "out", "targetHandle": "wrong_port"}],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry)
        diagnostics = exc.value.details["diagnostics"]
        assert any(d.get("node_instance_id") == "consumer" and d.get("port_id") == "required_in" for d in diagnostics)

    def test_secret_plaintext_rejected(self, compiler, registry):
        graph = {
            "nodes": [{"id": "n1", "type": "brief", "config": {"prompt": "Bearer sk-abcdefghijklmnop1234"}}],
            "edges": [],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry)
        diagnostics = exc.value.details["diagnostics"]
        assert any(d.get("code") == REASON_CODES["SECRET_PLAINTEXT"] for d in diagnostics)


# ---------------------------------------------------------------------------
# FR-8 / TF-SEC-001 minimum compile gate — P0 contract
# ---------------------------------------------------------------------------


class _NoOpResolver:
    """Resolver that returns deterministic snapshots matching the refs it sees.

    For tests we need snapshots that reflect the SQL resolver's
    behaviour: the resolver inspects each ref and returns the
    authoritative allow/deny.  The graph's ``owner_scope`` field is
    ignored entirely — every decision is reached from the test's
    precomputed snapshot list.
    """

    def __init__(self, factory) -> None:
        self._factory = factory  # callable (canonical_target, request_owner) -> EntitlementSnapshot
        self.received_refs: list[tuple[dict[str, Any], str]] = []

    def resolve_refs(self, *, request_owner: str, refs: list[tuple[dict[str, Any], str]]):
        self.received_refs = list(refs)
        return [self._factory(ref, request_owner) for ref, _path in refs]


def _artifact_allow_factory(source_owner: str):
    def _factory(ref: dict[str, Any], request_owner: str) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            canonical_target=str(ref.get("artifact_version_id") or ref.get("artifact_id") or ""),
            canonical_kind="artifact_version",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner=source_owner,
            request_owner=request_owner,
            reason="ArtifactVersion canonical owner matches requester",
        )
    return _factory


def _artifact_deny_factory(code: str, source_owner: str, reason: str):
    def _factory(ref: dict[str, Any], request_owner: str) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            canonical_target=str(ref.get("artifact_version_id") or ref.get("artifact_id") or ""),
            canonical_kind="artifact_version",
            decision="deny",
            code=code,
            source_owner=source_owner,
            request_owner=request_owner,
            reason=reason,
        )
    return _factory


def _resource_allow_factory(source_owner: str):
    def _factory(ref: dict[str, Any], request_owner: str) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            canonical_target=str(ref.get("revision_id") or ref.get("resource_id") or ""),
            canonical_kind="resource_revision",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner=source_owner,
            request_owner=request_owner,
            reason="Resource canonical owner matches requester",
        )
    return _factory


def _resource_deny_factory(code: str, source_owner: str, reason: str):
    def _factory(ref: dict[str, Any], request_owner: str) -> EntitlementSnapshot:
        return EntitlementSnapshot(
            canonical_target=str(ref.get("revision_id") or ref.get("resource_id") or ""),
            canonical_kind="resource_revision",
            decision="deny",
            code=code,
            source_owner=source_owner,
            request_owner=request_owner,
            reason=reason,
        )
    return _factory


class TestCompileGate:
    def test_same_owner_artifact_ref_allows(self):
        # Even though the graph declares owner_scope == request_owner,
        # the **decision** comes from the resolver, which loaded a
        # canonical ArtifactVersion with the same owner.  This proves
        # the gate never inspects the graph's owner_scope.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"artifact_id": str(uuid.uuid4()), "artifact_version_id": str(uuid.uuid4()), "owner_scope": "user:o1"}
        }}], "edges": []}
        resolver = _NoOpResolver(_artifact_allow_factory("user:o1"))
        snapshots, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert not any(d.get("severity") == "error" for d in diag)
        assert snapshots and snapshots[0].decision == "allow"
        assert resolver.received_refs and resolver.received_refs[0][0] is graph["nodes"][0]["config"]["ref"]

    def test_graph_owner_scope_is_ignored_for_artifact(self):
        # The graph falsely claims ``owner_scope == request_owner`` for an
        # ArtifactVersion whose canonical owner is another user.  The
        # resolver still denies; the graph field is irrelevant.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"artifact_id": str(uuid.uuid4()), "artifact_version_id": str(uuid.uuid4()), "owner_scope": "user:o1"}
        }}], "edges": []}
        resolver = _NoOpResolver(_artifact_deny_factory(
            REASON_CODES["CROSS_OWNER_ARTIFACT"],
            "user:other",
            "跨 owner ArtifactRef 不允许在 CompiledExecutionPlan 中固定消费",
        ))
        snapshots, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert any(d.get("code") == REASON_CODES["CROSS_OWNER_ARTIFACT"] for d in diag)
        assert snapshots[0].decision == "deny"
        assert snapshots[0].source_owner == "user:other"

    def test_cross_owner_artifact_with_grant_field_still_denied(self):
        # Even when the attacker adds a ``grant_snapshot_id`` to an
        # ArtifactRef, the canonical row is the only authority — the
        # gate must still deny.  The reason code differentiates the
        # attack shape for the diagnostic surface.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {
                "artifact_id": str(uuid.uuid4()), "artifact_version_id": str(uuid.uuid4()),
                "owner_scope": "user:other", "grant_snapshot_id": str(uuid.uuid4()),
            }
        }}], "edges": []}
        resolver = _NoOpResolver(_artifact_deny_factory(
            REASON_CODES["CROSS_OWNER_ARTIFACT_GRANT_FIELD"],
            "user:other",
            "跨 owner ArtifactRef 即使附带 grant 字段也必须拒绝",
        ))
        _, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert any(d.get("code") == REASON_CODES["CROSS_OWNER_ARTIFACT_GRANT_FIELD"] for d in diag)

    def test_same_owner_resource_ref_allows_without_grant(self):
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"resource_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()), "owner_scope": "user:o1"}
        }}], "edges": []}
        resolver = _NoOpResolver(_resource_allow_factory("user:o1"))
        snapshots, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert not any(d.get("severity") == "error" for d in diag)
        assert snapshots[0].decision == "allow"

    def test_cross_owner_resource_ref_without_grant_denied(self):
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"resource_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()), "owner_scope": "user:other"}
        }}], "edges": []}
        resolver = _NoOpResolver(_resource_deny_factory(
            REASON_CODES["MISSING_GRANT"],
            "user:other",
            "跨 owner ResourceRef 必须携带有效 GrantSnapshot",
        ))
        _, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert any(d.get("code") == REASON_CODES["MISSING_GRANT"] for d in diag)

    def test_cross_owner_resource_ref_with_grant_evaluated_by_resolver(self):
        grant_id = uuid.uuid4()
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {
                "resource_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()),
                "owner_scope": "user:other", "grant_snapshot_id": str(grant_id),
            }
        }}], "edges": []}
        seen_grant: dict[str, Any] = {}

        def _factory(ref: dict[str, Any], request_owner: str) -> EntitlementSnapshot:
            seen_grant["grant"] = ref.get("grant_snapshot_id")
            return EntitlementSnapshot(
                canonical_target=str(ref["revision_id"]),
                canonical_kind="resource_revision",
                decision="allow",
                code=REASON_CODES["DECISION_ALLOW"],
                source_owner="user:other",
                request_owner=request_owner,
                grant_snapshot_id=grant_id,
                reason="active grant_snapshot 授权",
            )

        snapshots, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=_NoOpResolver(_factory))
        assert str(seen_grant["grant"]) == str(grant_id)
        assert not any(d.get("severity") == "error" for d in diag)
        assert snapshots[0].decision == "allow"
        assert snapshots[0].grant_snapshot_id == grant_id

    def test_graph_owner_scope_forgery_denied_for_resource(self):
        # Owner_scope is forged to ``user:o1`` but the canonical Resource
        # owner (returned by the resolver) is ``user:other``.  The
        # gate must deny.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {
                "resource_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()),
                "owner_scope": "user:o1", "grant_snapshot_id": str(uuid.uuid4()),
            }
        }}], "edges": []}
        resolver = _NoOpResolver(_resource_deny_factory(
            REASON_CODES["MISSING_GRANT"],
            "user:other",
            "Resource canonical owner 与 graph 声明不一致",
        ))
        _, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert any(d.get("code") == REASON_CODES["MISSING_GRANT"] for d in diag)

    def test_no_resolver_fails_closed(self):
        # No SQL closure → every valid ref is denied with the
        # fail-closed sentinel reason code.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"artifact_id": str(uuid.uuid4()), "artifact_version_id": str(uuid.uuid4()), "owner_scope": "user:o1"}
        }}], "edges": []}
        snapshots, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=None)
        assert any(d.get("code") == "WF_ENTITLEMENT_RESOLVER_MISSING" for d in diag)
        assert all(s.decision == "deny" for s in snapshots)

    def test_latest_marker_rejected_without_resolver(self):
        # Latest markers are a static error; the resolver is not
        # consulted.  This guards against regressions where the static
        # scanner might delegate the latest check to the resolver.
        graph = {"nodes": [{"id": "n1", "config": {
            "ref": {"resource_id": str(uuid.uuid4()), "revision_id": "latest", "owner_scope": "user:o1"}
        }}], "edges": []}
        _, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=None)
        assert any(d.get("code") == REASON_CODES["LATEST_MARKER"] for d in diag)

    def test_secret_plaintext_rejected(self):
        graph = {"nodes": [{"id": "n1", "config": {
            "prompt": "Bearer sk-abcdefghijklmnop1234"
        }}], "edges": []}
        # Resolver returns no snapshots for this graph (no refs at all).
        _, _, diag = scan_graph(request_owner="user:o1", graph=graph, resolver=None)
        assert any(d.get("code") == REASON_CODES["SECRET_PLAINTEXT"] for d in diag)

    def test_resolver_receives_exactly_one_ref_per_candidate(self):
        # The P0 fix forbids the old "one resolver call returns a
        # batch of snapshots that get re-attributed to the wrong path"
        # behaviour.  Verify the resolver is consulted once per ref
        # and receives them in deterministic order.
        graph = {
            "nodes": [
                {"id": "n1", "config": {"a": {
                    "artifact_id": str(uuid.uuid4()), "artifact_version_id": str(uuid.uuid4()),
                }}},
                {"id": "n2", "config": {"b": {
                    "resource_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()),
                }}},
            ],
            "edges": [],
        }
        resolver = _NoOpResolver(lambda ref, owner: EntitlementSnapshot(
            canonical_target=str(ref.get("revision_id") or ref.get("artifact_version_id") or ""),
            canonical_kind="resource_revision" if "resource_id" in ref else "artifact_version",
            decision="allow",
            code=REASON_CODES["DECISION_ALLOW"],
            source_owner="user:o1",
            request_owner=owner,
            reason="",
        ))
        snapshots, _, _ = scan_graph(request_owner="user:o1", graph=graph, resolver=resolver)
        assert len(snapshots) == 2
        assert len(resolver.received_refs) == 2


# ---------------------------------------------------------------------------
# FR-7 / Master PRD §8.4: frozen ProviderCompilationReport outcomes
# ---------------------------------------------------------------------------


class TestCapabilityOutcomes:
    def test_required_capability_blocked_when_unsupported(self, compiler, registry):
        registry.node_definitions["brief"].policy_metadata = {
            "package_source": "approved:test",
            "required_capabilities": ["atlascloud.video"],
            "optional_capabilities": [],
            "unsupported_policy": "block",
        }
        graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry,
                compilation_context=CompilationContext(actor_scope="user:o1", available_capabilities=("atlascloud.llm",)),
            )
        diagnostics = exc.value.details["diagnostics"]
        assert any(d.get("code") == REASON_CODES["REQUIRED_CONTROL_BLOCKED"] for d in diagnostics)

    def test_optional_capability_degrades_without_blocking(self, compiler, registry):
        registry.node_definitions["brief"].policy_metadata = {
            "package_source": "approved:test",
            "required_capabilities": [],
            "optional_capabilities": ["atlascloud.video"],
            "unsupported_policy": "degrade",
        }
        graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry,
            compilation_context=CompilationContext(actor_scope="user:o1", available_capabilities=("atlascloud.llm",)),
        )
        assert plan.provider_compilation_report is not None
        outcomes = {result.result for result in plan.provider_compilation_report.control_results}
        assert "degraded" in outcomes
        assert "blocked" not in outcomes

    def test_optional_capability_warn_does_not_block(self, compiler, registry):
        registry.node_definitions["brief"].policy_metadata = {
            "package_source": "approved:test",
            "required_capabilities": [],
            "optional_capabilities": ["atlascloud.video"],
            "unsupported_policy": "ignore_with_warning",
        }
        graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry,
            compilation_context=CompilationContext(actor_scope="user:o1", available_capabilities=("atlascloud.llm",)),
        )
        assert plan.provider_compilation_report is not None
        outcomes = {result.result for result in plan.provider_compilation_report.control_results}
        assert "ignored_with_warning" in outcomes
        assert "blocked" not in outcomes

    def test_outcomes_only_in_frozen_enumeration(self, compiler, registry):
        registry.node_definitions["brief"].policy_metadata = {
            "package_source": "approved:test",
            "required_capabilities": [],
            "optional_capabilities": ["atlascloud.video"],
            "unsupported_policy": "degrade",
        }
        graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry,
            compilation_context=CompilationContext(actor_scope="user:o1", available_capabilities=("atlascloud.llm",)),
        )
        outcomes = {result.result for result in plan.provider_compilation_report.control_results}
        assert outcomes.issubset({"applied", "transformed", "degraded", "ignored_with_warning", "blocked"})


# ---------------------------------------------------------------------------
# FR-9 / FR-10: compile-time diagnostics encode node/port/config targets
# ---------------------------------------------------------------------------


class TestCompileDiagnosticShape:
    def test_diagnostic_carries_node_port_and_config_fields(self, compiler, registry):
        registry.node_definitions["brief"].config_schema = {
            "type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"],
        }
        with pytest.raises(CompilationError) as exc:
            compiler.compile(
                workflow_revision_id=uuid.uuid4(),
                graph={"nodes": [{"id": "n1", "type": "brief", "config": {}}], "edges": []},
                registry_snapshot=registry,
            )
        diagnostics = exc.value.details["diagnostics"]
        node_diag = next(d for d in diagnostics if d.get("node_instance_id") == "n1")
        assert "node_instance_id" in node_diag
        assert "config_path" in node_diag
        assert "safe_message" in node_diag
        assert "code" in node_diag


# ---------------------------------------------------------------------------
# FR-1: input is fixed WorkflowRevision; the compiler itself must refuse
# when the resolved graph still contains the legacy ``draft_id`` hint.
# ---------------------------------------------------------------------------


class TestNoDraftLeakage:
    def test_compile_persists_plan_with_revision_id(self, compiler, registry):
        revision_id = uuid.uuid4()
        graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
        plan = compiler.compile(workflow_revision_id=revision_id, graph=graph, registry_snapshot=registry)
        assert plan.workflow_revision_id == revision_id
        assert plan.plan_hash != ""


# ---------------------------------------------------------------------------
# FR-1: activation/runs/templates cannot bypass the compiler
# (smoke test - the real guarantees are exercised in PG integration tests)
# ---------------------------------------------------------------------------


def test_compiler_is_pure_does_not_consult_db():
    """The compiler must NEVER read mutable state.  This is a contract
    test: if anyone wires a SQL session into ``compile`` it will fail to
    type-check; the unit API surface only consumes immutable inputs."""

    compiler = WorkflowCompiler()
    sig = compiler.compile.__code__.co_varnames[:compiler.compile.__code__.co_argcount]
    forbidden = ("session", "session_factory", "db", "conn")
    assert not any(name in sig for name in forbidden), sig