"""P0 persistence and authorization contracts for WF-003/WF-005."""
from __future__ import annotations

import os
import hashlib
import json
from uuid import uuid4

import pytest

from src.core.exceptions import CrossOwnerError, ValidationError_
from src.domain.workflow.compiler import CompilationError, WorkflowCompiler
from src.domain.workflow.builtin_registry import ensure_public_business_node_baseline
from src.domain.workflow.sql_workflow_service import SqlWorkflowService
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.models import CompiledExecutionPlanModel, WorkflowRevisionModel
from src.infra.db.registry_repository import SqlRegistryService
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.session import get_session_factory
from src.schemas.models import OwnerScope


pytestmark = pytest.mark.skipif(os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1", reason="set TOONFLOW_RUN_PG_TESTS=1")


def _receipt(checksum: str, blob_id: str = "blob://world") -> dict[str, object]:
    return {
        "blob_id": blob_id,
        "checksum": checksum,
        "durability_class": "replicated",
        "checkpoint": "journal-2026-07-14T00:00:00Z",
        "protected_at": "2026-07-14T00:00:00Z",
        "restore_point_eligible": True,
        "verified": True,
    }


def _artifact(owner: OwnerScope):
    return SqlArtifactRepository().create_version(owner_scope=owner, schema_id="test/resource", schema_version=1, content_json={"v": 1})


def test_resource_cas_freeze_grant_and_restart_contract() -> None:
    owner, other = OwnerScope(kind="user", id=uuid4()), OwnerScope(kind="user", id=uuid4())
    repo = SqlResourceRepository()
    first = _artifact(owner)
    resource = repo.create(owner, "world", first.artifact_version_id)
    draft = repo.get_draft(resource.resource_id, owner)
    frozen = repo.freeze(resource.resource_id, owner, draft.draft_version)
    with pytest.raises(CrossOwnerError):
        repo.resolve_ref(resource.resource_id, frozen.revision_id, other, None)
    grant = repo.grant(frozen.revision_id, owner, other, capability_actions=["reference"])
    assert repo.resolve_ref(resource.resource_id, frozen.revision_id, other, grant).grant_snapshot_id == grant
    second = _artifact(owner)
    saved = repo.save_draft(resource.resource_id, owner, second.artifact_version_id, draft.draft_version)
    with pytest.raises(Exception):
        repo.save_draft(resource.resource_id, owner, second.artifact_version_id, draft.draft_version)
    next_revision = repo.freeze(resource.resource_id, owner, saved.draft_version)
    assert next_revision.created_from_artifact_version_id == first.artifact_version_id
    # A new repository instance must reconstruct canonical state from PostgreSQL.
    reloaded = SqlResourceRepository().get_draft(resource.resource_id, owner)
    assert reloaded.base_revision_id == next_revision.revision_id


def test_resource_canonical_projection_retains_blob_and_revision_provenance() -> None:
    """A rebuilt library projection must come only from immutable canonical rows."""
    owner = OwnerScope(kind="user", id=uuid4())
    artifact = SqlArtifactRepository().create_version(
        owner_scope=owner,
        schema_id="toonflow.world.v1",
        schema_version=1,
        content_json={"title": "World"},
        content_hash="immutable-hash",
        content_uri="s3://content/world.json",
        blob_uri="s3://durable-blob/world.json",
        metadata={"durability_receipt": _receipt("immutable-hash")},
    )
    repo = SqlResourceRepository()
    resource = repo.create(owner, "world", artifact.artifact_version_id)
    draft = repo.get_draft(resource.resource_id, owner)
    revision = repo.freeze(resource.resource_id, owner, draft.draft_version)
    provenance = repo.provenance(resource.resource_id, owner)
    assert provenance["draft_content"]["blob_uri"] == "s3://durable-blob/world.json"
    assert provenance["revisions"][0]["content"]["content_hash"] == "immutable-hash"
    rebuilt = SqlResourceRepository().rebuild_projection(owner)
    restored = next(row for row in rebuilt["resources"] if row["resource"]["resource_id"] == str(resource.resource_id))
    assert restored["revisions"][0]["revision_id"] == str(revision.revision_id)


def test_world_oc_elevation_and_blob_barrier_survive_canonical_rebuild() -> None:
    """An elevated OC can never lose its World revision/local-id origin."""
    owner = OwnerScope(kind="user", id=uuid4())
    artifacts = SqlArtifactRepository()
    with pytest.raises(ValidationError_) as pending_error:
        artifacts.create_version(
            owner_scope=owner,
            schema_id="toonflow.world.v1",
            schema_version=1,
            content_uri="s3://pending/world.json",
            content_hash="",
            metadata={},
        )
    assert pending_error.value.details["code"] == "BLOB_DURABILITY_BARRIER_REQUIRED"
    assert artifacts.list_versions(owner_scope=owner) == []

    embedded_oc = {
        "world_local_character_id": "characters.oc-a",
        "name": "OC A",
        "identity_core": {"role": "lead"},
    }
    world_artifact = artifacts.create_version(
        owner_scope=owner,
        schema_id="toonflow.world.v1",
        schema_version=1,
        content_uri="s3://durable/world.json",
        blob_uri="s3://durable/blob/world.json",
        content_hash="world-content-hash",
        metadata={"durability_receipt": _receipt("world-content-hash")},
        content_json={"embedded_characters": [embedded_oc]},
    )
    repo = SqlResourceRepository()
    world = repo.create(owner, "world", world_artifact.artifact_version_id)
    world_revision = repo.freeze(world.resource_id, owner, repo.get_draft(world.resource_id, owner).draft_version)
    oc_artifact = artifacts.create_version(
        owner_scope=owner,
        schema_id="toonflow.character.v1",
        schema_version=1,
        content_json=embedded_oc,
        content_hash="oc-content-hash",
    )
    oc = repo.create(
        owner,
        "character",
        oc_artifact.artifact_version_id,
        source_world_revision_id=world_revision.revision_id,
        source_local_id="characters.oc-a",
    )
    oc_revision = repo.freeze(oc.resource_id, owner, repo.get_draft(oc.resource_id, owner).draft_version)
    reloaded = SqlResourceRepository().provenance(oc.resource_id, owner)
    assert reloaded["resource"]["source_world_revision_id"] == str(world_revision.revision_id)
    assert reloaded["revisions"][0]["source_local_id"] == "characters.oc-a"
    assert reloaded["revisions"][0]["source_content_hash"] == hashlib.sha256(
        json.dumps(embedded_oc, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert reloaded["revisions"][0]["elevation_event_id"] == str(oc_revision.elevation_event_id)


def test_oc_elevation_rejects_nonexistent_local_id_or_noncopy_content() -> None:
    owner = OwnerScope(kind="user", id=uuid4())
    artifacts = SqlArtifactRepository()
    embedded_oc = {"world_local_character_id": "oc-1", "name": "Canonical"}
    world_artifact = artifacts.create_version(
        owner_scope=owner, schema_id="toonflow.world.v1", schema_version=1,
        content_json={"embedded_characters": [embedded_oc]},
    )
    repo = SqlResourceRepository()
    world = repo.create(owner, "world", world_artifact.artifact_version_id)
    world_revision = repo.freeze(world.resource_id, owner, 1)
    forged = artifacts.create_version(
        owner_scope=owner, schema_id="toonflow.character.v1", schema_version=1,
        content_json={"world_local_character_id": "oc-1", "name": "Forged"},
    )
    with pytest.raises(Exception, match="精确复制"):
        repo.create(owner, "character", forged.artifact_version_id,
                    source_world_revision_id=world_revision.revision_id, source_local_id="oc-1")
    copied = artifacts.create_version(
        owner_scope=owner, schema_id="toonflow.character.v1", schema_version=1,
        content_json=embedded_oc,
    )
    with pytest.raises(Exception, match="不存在"):
        repo.create(owner, "character", copied.artifact_version_id,
                    source_world_revision_id=world_revision.revision_id, source_local_id="not-present")
    promoted = repo.create(
        owner, "character", copied.artifact_version_id,
        source_world_revision_id=world_revision.revision_id, source_local_id="oc-1",
    )
    assert promoted.source_local_id == "oc-1"
    with pytest.raises(Exception, match="已提升"):
        repo.create(
            owner, "character", copied.artifact_version_id,
            source_world_revision_id=world_revision.revision_id, source_local_id="oc-1",
        )


def test_publication_requires_compilation_and_persists_plan() -> None:
    # Production publication consumes a frozen, approved RegistrySnapshot;
    # an empty ad-hoc snapshot is intentionally no longer a legal fixture.
    ensure_public_business_node_baseline()
    registry = SqlRegistryService().freeze_snapshot()
    assert "human_gate" in registry.node_definitions
    owner = OwnerScope(kind="user", id=uuid4())
    workflows = SqlWorkflowService()
    workflow = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(workflow.workflow_id)
    workflows.save_draft(workflow.workflow_id, {"nodes": [{"id": "gate", "type": "human_gate"}], "edges": []}, {}, {}, draft.graph_hash)
    confirmed = workflows.get_draft(workflow.workflow_id)
    revision, plan = workflows.publish_compiled_revision(
        workflow.workflow_id, registry, WorkflowCompiler(),
        expected_draft_hash=confirmed.full_draft_hash,
    )
    assert plan.workflow_revision_id == revision.revision_id
    with get_session_factory()() as session:
        assert session.get(CompiledExecutionPlanModel, plan.plan_id) is not None
    assert workflows.get_successful_plan(revision.revision_id).plan_id == plan.plan_id

    # A successful row is not sufficient on its own: its immutable payload
    # must still attest to the same plan/revision before runtime can consume it.
    with get_session_factory().begin() as session:
        row = session.get(CompiledExecutionPlanModel, plan.plan_id)
        assert row is not None
        tampered = dict(row.plan_json)
        tampered["workflow_revision_id"] = str(uuid4())
        row.plan_json = tampered
    with pytest.raises(Exception, match="CompiledExecutionPlan"):
        workflows.get_successful_plan(revision.revision_id)

    broken = workflows.create_workflow(owner_scope=owner)
    draft = workflows.get_draft(broken.workflow_id)
    workflows.save_draft(broken.workflow_id, {"nodes": [{"id": "x", "type": "unknown"}], "edges": []}, {}, {}, draft.graph_hash)
    broken_confirmed = workflows.get_draft(broken.workflow_id)
    with pytest.raises(CompilationError):
        workflows.publish_compiled_revision(
            broken.workflow_id, registry, WorkflowCompiler(),
            expected_draft_hash=broken_confirmed.full_draft_hash,
        )
    with get_session_factory()() as session:
        assert session.query(WorkflowRevisionModel).filter_by(workflow_id=broken.workflow_id).count() == 0
