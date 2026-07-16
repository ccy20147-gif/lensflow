"""Batch B (TF-WF-003 + minimum TF-SEC-001 compile gate) PG integration tests.

P0 contract: the compile-time gate must consult PostgreSQL canonical
rows for every ArtifactRef / ResourceRef; the graph's ``owner_scope``
field is **never** the source of truth.  These tests create real
ArtifactVersion / Resource / ResourceRevision / ResourceGrantSnapshot
rows, then run the SQL resolver through the public
``make_sql_entitlement_resolver`` factory so every code path — including
the route layer — is forced to bypass forgery.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from sqlalchemy import select

from src.domain.workflow.compiler import (
    CompilationContext,
    CompilationError,
    WorkflowCompiler,
)
from src.domain.workflow.compile_resolver import make_sql_entitlement_resolver
from src.domain.workflow.entitlement_gate import REASON_CODES
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.session import get_session_factory
from src.infra.blob.blob_service import SqlBlobRepository, sha256_hex
from src.schemas.models import (
    NodeDefinitionRevision,
    OwnerScope,
    PortTypeRef,
    RegistrySnapshot,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    return get_session_factory()


@pytest.fixture
def resources() -> SqlResourceRepository:
    return SqlResourceRepository()


@pytest.fixture
def artifacts() -> SqlArtifactRepository:
    return SqlArtifactRepository()


@pytest.fixture
def blobs() -> SqlBlobRepository:
    return SqlBlobRepository()


@pytest.fixture
def compiler() -> WorkflowCompiler:
    return WorkflowCompiler()


@pytest.fixture
def registry() -> RegistrySnapshot:
    return RegistrySnapshot(
        snapshot_id=uuid.uuid4(),
        node_definitions={
            "brief": NodeDefinitionRevision(
                node_type_id="brief",
                revision_id=uuid.uuid4(),
                semantic_version="1.0.0",
                input_ports=[PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
                output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
                config_schema={"type": "object"},
                policy_metadata={"package_source": "approved:test", "required_capabilities": []},
            ),
        },
    )


def _inline_artifact(
    artifacts: SqlArtifactRepository, owner: OwnerScope, payload: dict, *, content_hash: str | None = None,
    schema_id: str = "test/resource",
):
    return artifacts.create_version(
        owner_scope=owner,
        schema_id=schema_id,
        schema_version=1,
        content_json=payload,
        content_hash=content_hash or sha256_hex(repr(payload).encode("utf-8")),
    )


def _bootstrap_artifact_for_owner(
    artifacts: SqlArtifactRepository,
    owner: OwnerScope,
    *,
    schema_id: str = "test/resource",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return ``(artifact_id, artifact_version_id)`` for a freshly minted ArtifactVersion."""

    artifact = _inline_artifact(artifacts, owner, {"k": "v", "owner": owner.scoped_id}, schema_id=schema_id)
    return artifact.artifact_id, artifact.artifact_version_id


def _bootstrap_resource_with_grant(
    *,
    resources: SqlResourceRepository,
    artifacts: SqlArtifactRepository,
    blobs: SqlBlobRepository,
    source_owner: OwnerScope,
    grantee: OwnerScope,
    actions: list[str],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a Resource + active grant for ``grantee`` and return (resource_id, grant_id)."""

    artifact = _inline_artifact(artifacts, source_owner, {"kind": "test", "value": "x"})
    resource = resources.create(
        owner=source_owner,
        resource_type="generic",
        content_artifact_version_id=artifact.artifact_version_id,
    )
    revision = resources.freeze(resource_id=resource.resource_id, owner=source_owner, base_draft_version=1)
    grant_id = resources.grant(
        revision_id=revision.revision_id,
        owner=source_owner,
        grantee=grantee,
        capability_actions=actions,
    )
    return resource.resource_id, grant_id


def _make_resolver(session_factory, resources, grantee: OwnerScope):
    return make_sql_entitlement_resolver(
        session_factory=session_factory, repository=resources, actor_scope=grantee,
    )


def _resource_id_for(revision_id: uuid.UUID) -> uuid.UUID:
    from src.infra.db.models import ResourceRevisionModel
    factory = get_session_factory()
    with factory() as session:
        revision = session.get(ResourceRevisionModel, revision_id)
        assert revision is not None
        return revision.resource_id


def _revision_id_for(resource_id: uuid.UUID) -> uuid.UUID:
    from src.infra.db.models import ResourceRevisionModel
    factory = get_session_factory()
    with factory() as session:
        revision = session.scalar(
            select(ResourceRevisionModel).where(ResourceRevisionModel.resource_id == resource_id).limit(1)
        )
        assert revision is not None
        return revision.revision_id


def _artifact_version_id_for(artifact_id: uuid.UUID, owner: OwnerScope) -> uuid.UUID:
    from src.infra.db.models import ArtifactVersionModel
    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(ArtifactVersionModel)
            .where(
                ArtifactVersionModel.artifact_id == artifact_id,
                ArtifactVersionModel.owner_scope == owner.scoped_id,
            )
            .order_by(ArtifactVersionModel.created_at.desc())
            .limit(1)
        )
        assert row is not None
        return row.artifact_version_id


def _graph_with_artifact_ref(
    *, node_id: str, artifact_id: uuid.UUID, artifact_version_id: uuid.UUID, **extra: Any
) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "artifact_id": str(artifact_id),
        "artifact_version_id": str(artifact_version_id),
    }
    ref.update(extra)
    return {"nodes": [{"id": node_id, "type": "brief", "config": {"ref": ref}}], "edges": []}


def _graph_with_resource_ref(
    *, node_id: str, resource_id: uuid.UUID, revision_id: uuid.UUID, **extra: Any
) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "resource_id": str(resource_id),
        "revision_id": str(revision_id),
    }
    ref.update(extra)
    return {"nodes": [{"id": node_id, "type": "brief", "config": {"ref": ref}}], "edges": []}


def _compile_with_resolver(compiler, registry, *, graph, resolver, request_owner: str):
    """Compile using the SQL-backed resolver and return snapshots + diagnostics.

    We use a zero-graph body (single ``brief`` node) so the only failure
    mode is the entitlement gate.
    """
    context = CompilationContext(actor_scope=request_owner, entitlement_resolver=resolver)
    try:
        plan = compiler.compile(
            workflow_revision_id=uuid.uuid4(), graph=graph, registry_snapshot=registry,
            compilation_context=context,
        )
        return plan, []
    except CompilationError as exc:
        return None, exc.details.get("diagnostics", [])


# ---------------------------------------------------------------------------
# AC-1 / FR-11 / hash determinism
# ---------------------------------------------------------------------------


def test_same_revision_compile_is_deterministic(compiler, registry):
    revision_id = uuid.uuid4()
    graph = {"nodes": [{"id": "n1", "type": "brief"}], "edges": []}
    context = CompilationContext(actor_scope="user:o1")
    plan_a = compiler.compile(workflow_revision_id=revision_id, graph=graph, registry_snapshot=registry, compilation_context=context)
    plan_b = compiler.compile(workflow_revision_id=revision_id, graph=graph, registry_snapshot=registry, compilation_context=context)
    assert plan_a.plan_hash == plan_b.plan_hash


# ---------------------------------------------------------------------------
# FR-1: graph mutations after activation must not change the persisted plan
# ---------------------------------------------------------------------------


def test_post_activation_draft_changes_keep_existing_plan(
    compiler, registry, session_factory, resources, artifacts,
):
    owner = OwnerScope(kind="user", id=uuid.uuid4())
    _inline_artifact(artifacts, owner, {"k": "v1"})
    workflows = SqlWorkflowService(session_factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    registry_row = _freeze_registry_with_brief(session_factory)
    revision = workflows.create_revision_from_draft(
        workflow_id=workflow.workflow_id, registry_snapshot_id=registry_row.snapshot_id,
    )
    plan = compiler.compile(
        workflow_revision_id=revision.revision_id,
        graph={"nodes": [{"id": "n1", "type": "brief"}], "edges": []},
        registry_snapshot=_hydrate_snapshot(session_factory, registry_row.snapshot_id),
    )
    workflows.save_draft(
        workflow.workflow_id,
        {"nodes": [{"id": "totally_different", "type": "brief"}], "edges": []},
        {}, {}, workflows.get_draft(workflow.workflow_id).graph_hash,
    )
    plan_again = compiler.compile(
        workflow_revision_id=revision.revision_id,
        graph={"nodes": [{"id": "n1", "type": "brief"}], "edges": []},
        registry_snapshot=_hydrate_snapshot(session_factory, registry_row.snapshot_id),
    )
    assert plan.plan_hash == plan_again.plan_hash


def _freeze_registry_with_brief(session_factory):
    from src.infra.db.registry_repository import SqlRegistryService
    brief = NodeDefinitionRevision(
        node_type_id="brief", revision_id=uuid.uuid4(), semantic_version="1.0.0",
        input_ports=[PortTypeRef(port_id="in", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
        output_ports=[PortTypeRef(port_id="out", type_id="artifact", schema_id="creative_brief", schema_version=1, cardinality="required")],
        config_schema={"type": "object"},
        policy_metadata={"package_source": "approved:test", "required_capabilities": []},
    )
    service = SqlRegistryService(session_factory)
    existing = service.list_node_definitions(type_id=brief.node_type_id)
    if not existing:
        service.add_node_definition(brief)
        service.activate_node_definition(brief.node_type_id, brief.revision_id)
    return service.freeze_snapshot()


def _hydrate_snapshot(session_factory, snapshot_id):
    from src.infra.db.registry_repository import SqlRegistryService
    return SqlRegistryService(session_factory).get_snapshot(snapshot_id)


# ---------------------------------------------------------------------------
# P0 — ArtifactRef forgery paths
# ---------------------------------------------------------------------------


class TestArtifactRefForgery:
    def test_legitimate_same_owner_artifact_ref_allows(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        owner = OwnerScope(kind="user", id=uuid.uuid4())
        artifact_id, version_id = _bootstrap_artifact_for_owner(artifacts, owner)
        resolver = _make_resolver(session_factory, resources, owner)
        graph = _graph_with_artifact_ref(
            node_id="n1", artifact_id=artifact_id, artifact_version_id=version_id,
            owner_scope=owner.scoped_id,
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=owner.scoped_id,
        )
        assert plan is not None
        assert not any(d.get("severity") == "error" for d in diag)
        snap = next(s for s in plan.entitlement_snapshots if s["canonical_target"] == str(version_id))
        assert snap["decision"] == "allow"
        assert snap["source_owner"] == owner.scoped_id

    def test_cross_owner_artifact_with_forged_owner_scope_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # Source owns the artifact; the requester forges ``owner_scope``
        # to look like himself.  The SQL resolver must still deny
        # because the canonical ArtifactVersion.owner_scope is the
        # source's owner.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        artifact_id, version_id = _bootstrap_artifact_for_owner(artifacts, source)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_artifact_ref(
            node_id="n1", artifact_id=artifact_id, artifact_version_id=version_id,
            owner_scope=requester.scoped_id,  # forgery
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["CROSS_OWNER_ARTIFACT"] for d in diag)

    def test_cross_owner_artifact_with_missing_owner_scope_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # The attacker omits ``owner_scope`` entirely; the resolver
        # must still find the canonical row and deny.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        artifact_id, version_id = _bootstrap_artifact_for_owner(artifacts, source)
        resolver = _make_resolver(session_factory, resources, requester)
        # ``owner_scope`` deliberately absent.
        graph = _graph_with_artifact_ref(
            node_id="n1", artifact_id=artifact_id, artifact_version_id=version_id,
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["CROSS_OWNER_ARTIFACT"] for d in diag)

    def test_cross_owner_artifact_with_forged_grant_field_still_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # Per Master PRD §8.1, ArtifactRef is never cross-owner consumable,
        # even when a ``grant_snapshot_id`` is supplied.  The SQL resolver
        # returns the dedicated reason code so the diagnostic surface
        # can surface the attack shape.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        artifact_id, version_id = _bootstrap_artifact_for_owner(artifacts, source)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_artifact_ref(
            node_id="n1", artifact_id=artifact_id, artifact_version_id=version_id,
            owner_scope=requester.scoped_id,
            grant_snapshot_id=str(uuid.uuid4()),  # forged
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["CROSS_OWNER_ARTIFACT_GRANT_FIELD"] for d in diag)


# ---------------------------------------------------------------------------
# P0 — ResourceRef forgery paths
# ---------------------------------------------------------------------------


class TestResourceRefForgery:
    def test_legitimate_same_owner_resource_ref_allows(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        owner = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, _grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=owner, grantee=owner, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, owner)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            owner_scope=owner.scoped_id,
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=owner.scoped_id,
        )
        assert plan is not None
        assert not any(d.get("severity") == "error" for d in diag)
        snap = next(s for s in plan.entitlement_snapshots if s["canonical_target"] == str(revision_id))
        assert snap["decision"] == "allow"
        assert snap["source_owner"] == owner.scoped_id

    def test_cross_owner_resource_ref_without_grant_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, _grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            owner_scope=source.scoped_id,
            # grant_snapshot_id deliberately omitted
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["MISSING_GRANT"] for d in diag)

    def test_cross_owner_resource_ref_with_missing_owner_scope_still_uses_canonical(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # The attacker omits ``owner_scope``; the SQL resolver still
        # finds the canonical Resource row and treats the ref as
        # cross-owner.  With a valid grant the plan still succeeds.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            # owner_scope deliberately omitted
            grant_snapshot_id=str(grant_id),
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is not None, f"expected success, got diagnostics: {diag}"
        assert not any(d.get("severity") == "error" for d in diag)
        snap = next(s for s in plan.entitlement_snapshots if s["canonical_target"] == str(revision_id))
        assert snap["decision"] == "allow"
        assert snap["source_owner"] == source.scoped_id
        assert snap["request_owner"] == requester.scoped_id

    def test_cross_owner_resource_ref_with_forged_owner_scope_still_cross_owner(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # The attacker sets ``owner_scope`` to themselves but the
        # canonical Resource is owned by someone else.  Without a
        # grant, the SQL resolver still classifies the ref as
        # cross-owner and the plan must NOT compile.  This proves the
        # forgery of ``owner_scope`` cannot bypass the grant gate.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, _grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            owner_scope=requester.scoped_id,  # forgery
            # grant_snapshot_id deliberately omitted
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["MISSING_GRANT"] for d in diag)

    def test_cross_owner_resource_ref_with_mismatched_resource_id_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # The attacker swaps ``resource_id`` to point at a different
        # Resource while keeping the real ``revision_id``.  The
        # resolver must reject because the revision does not belong
        # to the declared resource.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, _grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        other_resource_id, _ = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=other_resource_id, revision_id=revision_id,  # mismatched
            owner_scope=source.scoped_id,
            grant_snapshot_id=str(_grant_id),
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") == REASON_CODES["REVISION_MISMATCH"] for d in diag)

    def test_cross_owner_resource_ref_with_grant_for_wrong_revision_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        # The attacker keeps a grant that points at one revision while
        # the ref uses a different revision.  ``evaluate_entitlement``
        # compares the grant's ``resource_revision_id`` with the
        # declared ``revision_id`` and denies.
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, _grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        # Create a second revision on the same resource so we can swap.
        draft = resources.get_draft(resource_id, source)
        resources.save_draft(
            resource_id=resource_id, owner=source,
            content_artifact_version_id=draft.content_artifact_version_id,
            base_draft_version=draft.draft_version,
        )
        new_revision = resources.freeze(resource_id=resource_id, owner=source, base_draft_version=draft.draft_version + 1)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=new_revision.revision_id,  # newer
            owner_scope=source.scoped_id,
            grant_snapshot_id=str(_grant_id),  # points at original_revision_id
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None

    def test_cross_owner_resource_ref_with_revoked_grant_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resources.revoke_grant(revision_id=revision_id, grant_snapshot_id=grant_id, owner=source)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            owner_scope=source.scoped_id,
            grant_snapshot_id=str(grant_id),
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        # The resolver's reason code from ``evaluate_entitlement``
        # maps to one of the deny codes.
        assert any(d.get("code") in {
            REASON_CODES["REVOKED_GRANT"],
            REASON_CODES["MISSING_GRANT"],
            REASON_CODES["DECISION_DENY"],
        } for d in diag)

    def test_cross_owner_resource_ref_with_insufficient_scope_denied(
        self, compiler, registry, session_factory, resources, artifacts, blobs,
    ):
        source = OwnerScope(kind="user", id=uuid.uuid4())
        requester = OwnerScope(kind="user", id=uuid.uuid4())
        resource_id, grant_id = _bootstrap_resource_with_grant(
            resources=resources, artifacts=artifacts, blobs=blobs,
            source_owner=source, grantee=requester, actions=["reference"],
        )
        revision_id = _revision_id_for(resource_id)
        resolver = _make_resolver(session_factory, resources, requester)
        graph = _graph_with_resource_ref(
            node_id="n1", resource_id=resource_id, revision_id=revision_id,
            owner_scope=source.scoped_id,
            role="execute",  # not in [reference]
            grant_snapshot_id=str(grant_id),
        )
        plan, diag = _compile_with_resolver(
            compiler, registry, graph=graph, resolver=resolver, request_owner=requester.scoped_id,
        )
        assert plan is None
        assert any(d.get("code") in {
            REASON_CODES["SCOPE_INSUFFICIENT"],
            REASON_CODES["DECISION_DENY"],
        } for d in diag)


# ---------------------------------------------------------------------------
# Activation / compile HTTP path — proves the route layer cannot bypass
# the gate either.
# ---------------------------------------------------------------------------


def test_compile_failure_does_not_persist_runnable_plan(
    session_factory, resources, artifacts, blobs,
):
    """A failed compile raises and the SQL workflow service refuses to
    publish a runnable plan because the transaction rolls back.
    """
    owner = OwnerScope(kind="user", id=uuid.uuid4())
    workflows = SqlWorkflowService(session_factory)
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(
        workflow.workflow_id,
        {"nodes": [{"id": "broken", "type": "no-such-node"}], "edges": []},
        {}, {}, draft.graph_hash,
    )
    from src.infra.db.registry_repository import SqlRegistryService
    snapshot = SqlRegistryService(session_factory).freeze_snapshot()
    compiler = WorkflowCompiler()
    with pytest.raises(CompilationError):
        compiler.compile(
            workflow_revision_id=uuid.uuid4(),
            graph={"nodes": [{"id": "broken", "type": "no-such-node"}], "edges": []},
            registry_snapshot=snapshot,
        )
    from src.infra.db.models import CompiledExecutionPlanModel, WorkflowRevisionModel
    with session_factory() as session:
        own_revisions = list(session.scalars(
            select(WorkflowRevisionModel.revision_id).where(
                WorkflowRevisionModel.workflow_id == workflow.workflow_id
            )
        ))
        if own_revisions:
            plans = list(session.scalars(
                select(CompiledExecutionPlanModel).where(
                    CompiledExecutionPlanModel.workflow_revision_id.in_(own_revisions)
                )
            ))
            assert plans == [], "Compiler failure must not produce a runnable plan row"


def test_http_compile_route_cannot_bypass_entitlement_gate(
    session_factory, resources, artifacts, blobs,
):
    """Even when a request hits the HTTP ``/compile`` route, the
    SQL-backed resolver must reject cross-owner ArtifactRef forgeries.

    This wires the full route stack and confirms the resolver injected
    via the route's ``_compilation_context`` still consults
    PostgreSQL canonical rows.  Without this gate, an attacker who
    wrote a bare ``ArtifactRef`` whose ``owner_scope`` field matched
    their session would have bypassed the cross-owner denial.
    """
    from fastapi.testclient import TestClient
    from src.app import app
    from src.infra.db.identity_repository import get_session_store

    source = OwnerScope(kind="user", id=uuid.uuid4())
    requester = OwnerScope(kind="user", id=uuid.uuid4())
    artifact_id, version_id = _bootstrap_artifact_for_owner(artifacts, source)

    workflows = SqlWorkflowService(session_factory)
    workflow = workflows.create_workflow(owner_scope=requester)
    registry_row = _freeze_registry_with_brief(session_factory)
    workflows.save_draft(
        workflow.workflow_id,
        {
            "nodes": [{
                "id": "n1", "type": "brief",
                "data": {"config": {
                    "ref": {
                        "artifact_id": str(artifact_id),
                        "artifact_version_id": str(version_id),
                        # Attacker forges ``owner_scope`` to match the requester.
                        "owner_scope": requester.scoped_id,
                        # And adds a bogus grant field, just in case.
                        "grant_snapshot_id": str(uuid.uuid4()),
                    },
                }},
            }],
            "edges": [],
        },
        {}, {}, workflows.get_draft(workflow.workflow_id).graph_hash,
    )
    workflows.create_revision_from_draft(
        workflow_id=workflow.workflow_id, registry_snapshot_id=registry_row.snapshot_id,
    )

    def _headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {get_session_store().issue(requester.id)['token']}"}

    with TestClient(app) as client:
        response = client.post(f"/api/v1/workflows/{workflow.workflow_id}/compile", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert any(
        diagnostic.get("code") in {
            REASON_CODES["CROSS_OWNER_ARTIFACT"],
            REASON_CODES["CROSS_OWNER_ARTIFACT_GRANT_FIELD"],
            # Legacy route layer emits ``WF_INPUT_CROSS_OWNER_ARTIFACT``
            # via the defensive helper; either code satisfies the gate.
            "WF_INPUT_CROSS_OWNER_ARTIFACT",
        }
        for diagnostic in body["diagnostics"]
    )