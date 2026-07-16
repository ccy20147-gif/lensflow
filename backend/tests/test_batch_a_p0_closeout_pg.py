"""Batch A P0 close-out tests (PG-gated).

Closes the four P0 gaps surfaced in the Batch A verification:

1. ResourceDraft CAS rowcount under real concurrent load.
2. Promotion gate cannot be bypassed via the generic create() path;
   resolve/create must be a single transaction; cross-owner supersede
   grief is rejected.
3. ArtifactVersion + canonical LineageEdge are immutable across a
   projection rebuild.
4. Blob finalize_delete re-scans references and refuses late racers.
"""
from __future__ import annotations

import io
import os
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.core.exceptions import (
    ConflictError,
    CrossOwnerError,
    ValidationError_,
)
from src.infra.blob.blob_service import SqlBlobRepository, sha256_hex
from src.infra.db.artifact_repository import SqlArtifactRepository
from src.infra.db.models import (
    ArtifactVersionModel,
    BlobModel,
    BlobReferenceIndexModel,
    CandidateSetModel,
    LineageEdgeModel,
    LineageEdgeProjectionModel,
    NodeRunAttemptModel,
    NodeRunModel,
    OutputBindingSupersedeModel,
    ProviderInvocationAttemptModel,
    ProviderInvocationRecordModel,
    ProviderOutputBindingModel,
    ResourceRevisionModel,
    SelectionRecordModel,
    WorkflowModel,
    WorkflowRevisionModel,
    WorkflowRunModel,
)
from src.infra.db.resource_repository import SqlResourceRepository
from src.infra.db.session import get_session_factory
from src.schemas.enums import BlobStatus
from src.schemas.models import OwnerScope, PromotionSource


pytestmark = pytest.mark.skipif(
    os.environ.get("TOONFLOW_RUN_PG_TESTS") != "1",
    reason="set TOONFLOW_RUN_PG_TESTS=1",
)


@pytest.fixture
def owner_a() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def owner_b() -> OwnerScope:
    return OwnerScope(kind="user", id=uuid4())


@pytest.fixture
def artifacts() -> SqlArtifactRepository:
    return SqlArtifactRepository()


@pytest.fixture
def resources() -> SqlResourceRepository:
    return SqlResourceRepository()


@pytest.fixture
def blobs() -> SqlBlobRepository:
    return SqlBlobRepository()


def _inline_artifact(
    artifacts: SqlArtifactRepository,
    owner: OwnerScope,
    payload: dict,
    *,
    schema_id: str = "p0-close/text",
    content_hash: str | None = None,
) -> ArtifactVersionModel:
    return artifacts.create_version(
        owner_scope=owner, schema_id=schema_id, schema_version=1,
        content_json=payload,
        content_hash=content_hash or sha256_hex(repr(payload).encode("utf-8")),
    )


def _upload_blob(blobs: SqlBlobRepository, owner: OwnerScope, payload: bytes):
    upload = blobs.start_upload(
        owner, expected_size_bytes=len(payload),
        expected_content_hash=sha256_hex(payload),
        media_type="application/octet-stream", idempotency_key=str(uuid4()),
    )
    return blobs.complete_upload(
        upload.session_id, owner, io.BytesIO(payload),
        declared_size=len(payload), declared_hash=sha256_hex(payload),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_output_binding(owner: OwnerScope, artifact_version_id: UUID) -> UUID:
    factory = get_session_factory()
    with factory.begin() as session:
        workflow = WorkflowModel(owner_scope=owner.scoped_id)
        session.add(workflow)
        session.flush()
        revision = WorkflowRevisionModel(
            workflow_id=workflow.workflow_id, revision_number=1,
            graph_hash="seed", execution_hash="seed",
            registry_snapshot_id=uuid4(),
        )
        session.add(revision)
        session.flush()
        run = WorkflowRunModel(
            workflow_revision_id=revision.revision_id,
            compiled_plan_id=uuid4(), owner_scope=owner.scoped_id,
        )
        session.add(run)
        session.flush()
        node_run = NodeRunModel(
            run_id=run.run_id, node_instance_id="seed", node_type_id="seed",
        )
        session.add(node_run)
        session.flush()
        attempt = NodeRunAttemptModel(
            node_run_id=node_run.node_run_id, attempt_number=1, execution_epoch=1,
        )
        session.add(attempt)
        session.flush()
        provider_attempt = ProviderInvocationAttemptModel(
            node_run_attempt_id=attempt.attempt_id,
            provider_id="seed", model_id="seed",
            idempotency_key=f"seed-{uuid4()}", request_body_hash="seed",
        )
        session.add(provider_attempt)
        session.flush()
        record = ProviderInvocationRecordModel(
            provider_attempt_id=provider_attempt.provider_attempt_id,
            provider_id="seed", model_id="seed", model_version="seed",
            idempotency_key=f"seed-{uuid4()}", request_body_hash="seed",
            response_fingerprint="seed", usage={}, actual_cost=0.0,
            started_at=_now(), completed_at=_now(),
        )
        session.add(record)
        session.flush()
        binding = ProviderOutputBindingModel(
            binding_id=uuid4(), record_id=record.record_id,
            output_artifact_version_id=artifact_version_id,
            output_index=0, output_label="seed", owner_scope=owner.scoped_id,
        )
        session.add(binding)
        session.flush()
        return binding.binding_id  # type: ignore[return-value]


def _seed_selection_record(owner: OwnerScope, selected_refs: list[dict[str, Any]]) -> UUID:
    factory = get_session_factory()
    with factory.begin() as session:
        candidate_set = CandidateSetModel(
            candidate_set_id=uuid4(), owner_scope=owner.scoped_id,
            run_id=None, node_run_id=None,
            candidate_refs=[], failed_candidates=[], cost_allocation={},
        )
        session.add(candidate_set)
        session.flush()
        selection = SelectionRecordModel(
            selection_id=uuid4(), candidate_set_id=candidate_set.candidate_set_id,
            owner_scope=owner.scoped_id, ranking=[],
            selected_refs=selected_refs,
            actor_or_model="seed", rubric_revision="seed", rationale="",
        )
        session.add(selection)
        session.flush()
        return selection.selection_id  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# P0 #1 — ResourceDraft CAS rowcount under real concurrency
# ---------------------------------------------------------------------------


def test_p0_cas_rowcount_rejects_loser_under_real_concurrency(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """Two threads issue save_draft concurrently with the same base_draft_version.

    Only one rowcount=1 transaction may commit; the other MUST see
    rowcount=0, re-read the now-current draft, and raise a structured
    ConflictError.  The final persisted draft must equal the winner's
    payload, never the loser's.
    """
    base = _inline_artifact(artifacts, owner_a, {"v": 1})
    resource = resources.create(owner_a, "world", base.artifact_version_id)
    draft = resources.get_draft(resource.resource_id, owner_a)
    base_version = draft.draft_version

    winner_payload = _inline_artifact(artifacts, owner_a, {"v": "WINNER"})
    loser_payload = _inline_artifact(artifacts, owner_a, {"v": "LOSER"})

    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def attempt(content_av_id: UUID, label: str) -> None:
        try:
            barrier.wait(timeout=5)
            saved = resources.save_draft(
                resource.resource_id, owner_a, content_av_id, base_version,
            )
            results[label] = ("ok", saved)
        except ConflictError as exc:
            results[label] = ("conflict", exc)
        except Exception as exc:  # noqa: BLE001
            results[label] = ("error", exc)

    t1 = threading.Thread(target=attempt, args=(winner_payload.artifact_version_id, "A"))
    t2 = threading.Thread(target=attempt, args=(loser_payload.artifact_version_id, "B"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    outcomes = [results["A"][0], results["B"][0]]
    assert outcomes.count("ok") == 1, f"expected exactly one winner, got {results!r}"
    assert outcomes.count("conflict") == 1, f"expected exactly one conflict, got {results!r}"

    winner_label = "A" if results["A"][0] == "ok" else "B"
    loser_label = "B" if winner_label == "A" else "A"
    winner_payload_artifact = (
        winner_payload.artifact_version_id if winner_label == "A"
        else loser_payload.artifact_version_id
    )
    loser_payload_artifact = (
        winner_payload.artifact_version_id if loser_label == "A"
        else loser_payload.artifact_version_id
    )

    loser_conflict = results[loser_label][1]
    assert loser_conflict.details is not None
    assert loser_conflict.details["operation"] == "save_draft"
    assert loser_conflict.details["base_draft_version"] == base_version
    assert loser_conflict.details["current_content_artifact_version_id"] == str(winner_payload_artifact)
    assert loser_conflict.details["proposed_content_artifact_version_id"] == str(loser_payload_artifact)

    final = resources.get_draft(resource.resource_id, owner_a)
    assert final.content_artifact_version_id == winner_payload_artifact
    assert final.draft_version == base_version + 1


# ---------------------------------------------------------------------------
# P0 #2 — Promotion gate cannot be bypassed
# ---------------------------------------------------------------------------


def test_p0_promote_create_persists_provenance_in_one_transaction(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """A successful promotion persists kind+ref_id+artifact_version_id
    on the Resource row, and that provenance is read-only afterwards.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    source = PromotionSource(kind="output_binding", binding_id=binding_id)

    resource, meta = resources.promote_from_source(owner_a, source, "world")
    assert resource.promotion_source_kind == "output_binding"
    assert resource.promotion_source_ref_id == binding_id
    assert resource.promotion_source_artifact_version_id == target.artifact_version_id
    assert meta["artifact_version_id"] == str(target.artifact_version_id)

    fresh = SqlResourceRepository().get(resource.resource_id, owner_a)
    assert fresh.promotion_source_kind == "output_binding"
    assert fresh.promotion_source_ref_id == binding_id


def test_p0_promote_rejects_bare_artifact_id(
    resources: SqlResourceRepository, owner_a: OwnerScope,
) -> None:
    with pytest.raises(ConflictError):
        resources.promote_from_source(
            owner_a, PromotionSource(kind="artifact"),  # type: ignore[arg-type]
            "world",
        )


def test_p0_promote_rejects_forged_source_pointing_to_wrong_artifact(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """The contract pins the resolved ArtifactVersion to the binding —
    a caller cannot pick a different ArtifactVersion via the source.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": "binding-target"})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    fake_artifact = _inline_artifact(artifacts, owner_a, {"v": "fake"})
    source = PromotionSource(
        kind="output_binding", binding_id=binding_id, output_index=99,
    )
    resolved_id, meta = resources.resolve_promotion_source(owner_a, source)
    assert resolved_id == target.artifact_version_id
    assert meta["artifact_version_id"] == str(target.artifact_version_id)
    assert fake_artifact.artifact_version_id != resolved_id


def test_p0_promote_rejects_cross_owner_supersede_grief(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
    owner_b: OwnerScope,
) -> None:
    """Tenant B MUST NOT grief tenant A's promotion source."""
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    source = PromotionSource(kind="output_binding", binding_id=binding_id)
    with pytest.raises(CrossOwnerError):
        resources.supersede_promotion_source(
            owner_b, "output_binding", binding_id, reason="grief",
        )
    resource, _ = resources.promote_from_source(owner_a, source, "world")
    assert resource.promotion_source_kind == "output_binding"


def test_p0_promote_rejects_supersede_written_by_owner_before_promote(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    resources.supersede_promotion_source(owner_a, "output_binding", binding_id, reason="rerun")
    with pytest.raises(ConflictError):
        resources.promote_from_source(
            owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
        )


def test_p0_generic_create_rejects_run_output_with_existing_binding(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """``create`` MUST refuse a bootstrap when the ArtifactVersion is
    already cited by a same-owner OutputBinding.  The contract is that
    such a Resource is reachable ONLY through ``promote_from_source``.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    with pytest.raises(ConflictError) as exc_info:
        resources.create(owner_a, "world", target.artifact_version_id)
    assert exc_info.value.details is not None
    assert exc_info.value.details["binding_id"] == str(binding_id)
    assert exc_info.value.details["artifact_version_id"] == str(target.artifact_version_id)
    # The genuine promotion path is still the only correct one.
    promoted, _ = resources.promote_from_source(
        owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
    )
    assert promoted.promotion_source_kind == "output_binding"


def test_p0_generic_create_rejects_run_output_with_existing_selection_record(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """``create`` MUST refuse a bootstrap when the ArtifactVersion is
    listed inside any same-owner ``SelectionRecord.selected_refs``.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    selection_id = _seed_selection_record(
        owner_a,
        [{"artifact_version_id": str(target.artifact_version_id)}],
    )
    with pytest.raises(ConflictError) as exc_info:
        resources.create(owner_a, "shot_plan", target.artifact_version_id)
    assert exc_info.value.details is not None
    assert exc_info.value.details["selection_id"] == str(selection_id)
    promoted, _ = resources.promote_from_source(
        owner_a, PromotionSource(kind="selection_record", selection_id=selection_id),
        "shot_plan",
    )
    assert promoted.promotion_source_kind == "selection_record"


def test_p0_generic_create_allows_unbound_artifact(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """A bootstrap for a fresh ArtifactVersion with no binding or
    selection reference is the legitimate bootstrap path.
    """
    art = _inline_artifact(artifacts, owner_a, {"v": "fresh"})
    resource = resources.create(owner_a, "world", art.artifact_version_id)
    assert resource.promotion_source_kind == "bootstrap"
    assert resource.promotion_source_ref_id is None
    assert resource.promotion_source_artifact_version_id == art.artifact_version_id


def _seed_selection_with_key(
    owner: OwnerScope, key: str, artifact_version_id: UUID,
) -> UUID:
    """Seed a SelectionRecord whose ``selected_refs`` uses the given
    ref key to point at the supplied ArtifactVersion.  Returns the
    selection id.
    """
    factory = get_session_factory()
    with factory.begin() as session:
        candidate_set = CandidateSetModel(
            candidate_set_id=uuid4(), owner_scope=owner.scoped_id,
            run_id=None, node_run_id=None,
            candidate_refs=[], failed_candidates=[], cost_allocation={},
        )
        session.add(candidate_set)
        session.flush()
        selection = SelectionRecordModel(
            selection_id=uuid4(), candidate_set_id=candidate_set.candidate_set_id,
            owner_scope=owner.scoped_id, ranking=[],
            selected_refs=[{key: str(artifact_version_id)}],
            actor_or_model="seed", rubric_revision="seed", rationale="",
        )
        session.add(selection)
        session.flush()
        return selection.selection_id  # type: ignore[return-value]


@pytest.mark.parametrize("ref_key", [
    "artifact_version_id",
    "artifactVersionId",
    "output_artifact_version_id",
])
def test_p0_bootstrap_blocked_for_every_ref_key(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
    ref_key: str,
) -> None:
    """For each of the three recognised ref keys, a SelectionRecord
    citing the ArtifactVersion under that key MUST cause the
    bootstrap ``create()`` to raise ``ConflictError``.  The promote
    path, by contrast, MUST accept the same ref and persist the
    correct provenance.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": ref_key})
    selection_id = _seed_selection_with_key(owner_a, ref_key, target.artifact_version_id)

    with pytest.raises(ConflictError) as exc_info:
        resources.create(owner_a, "shot_plan", target.artifact_version_id)
    assert exc_info.value.details is not None
    assert exc_info.value.details["selection_id"] == str(selection_id)
    assert exc_info.value.details["artifact_version_id"] == str(target.artifact_version_id)

    promoted, _ = resources.promote_from_source(
        owner_a, PromotionSource(kind="selection_record", selection_id=selection_id),
        "shot_plan",
    )
    assert promoted.promotion_source_kind == "selection_record"
    assert promoted.promotion_source_artifact_version_id == target.artifact_version_id


def test_p0_promote_supersede_race_supersede_first(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """supersede 先提交：后续 promote 必须拒绝。"""
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    resources.supersede_promotion_source(owner_a, "output_binding", binding_id, reason="rerun")
    with pytest.raises(ConflictError):
        resources.promote_from_source(
            owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
        )


def test_p0_promote_supersede_race_promote_first(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """promote 先提交：committed Resource 的 provenance 合法；后续
    supersede 只阻断**未来**的 promote，不破坏已 commit 的 Resource。"""
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    resource, _ = resources.promote_from_source(
        owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
    )
    assert resource.promotion_source_kind == "output_binding"
    # Late supersede: Resource row stays intact, but future promote is blocked.
    resources.supersede_promotion_source(owner_a, "output_binding", binding_id, reason="rerun")
    fresh = SqlResourceRepository().get(resource.resource_id, owner_a)
    assert fresh.promotion_source_kind == "output_binding"
    assert fresh.promotion_source_artifact_version_id == target.artifact_version_id
    with pytest.raises(ConflictError):
        resources.promote_from_source(
            owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
        )


def test_p0_promote_supersede_concurrent_threads_serialise(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    """Two real PostgreSQL transactions race promote vs. supersede on
    the same source row.  The shared ``SELECT ... FOR UPDATE`` on
    the OutputBinding row MUST serialise them; the result MUST be
    consistent: either the promote wins and the Resource is
    persisted, or the supersede wins and any future promote is
    rejected.  A run that "both committed" would be a contract
    violation.
    """
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def do_promote() -> None:
        try:
            barrier.wait(timeout=5)
            resource, _ = resources.promote_from_source(
                owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
            )
            results["promote"] = ("ok", resource)
        except ConflictError as exc:
            results["promote"] = ("conflict", exc)
        except Exception as exc:  # noqa: BLE001
            results["promote"] = ("error", exc)

    def do_supersede() -> None:
        try:
            barrier.wait(timeout=5)
            sid = resources.supersede_promotion_source(
                owner_a, "output_binding", binding_id, reason="race",
            )
            results["supersede"] = ("ok", sid)
        except Exception as exc:  # noqa: BLE001
            results["supersede"] = ("error", exc)

    t1 = threading.Thread(target=do_promote)
    t2 = threading.Thread(target=do_supersede)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Both must complete without unhandled errors.
    assert "promote" in results, results
    assert "supersede" in results, results
    assert results["supersede"][0] == "ok", f"supersede must always succeed: {results['supersede']!r}"

    # After both have committed, the visible state MUST be one of two
    # consistent outcomes:
    #
    #   * promote won → Resource row exists with kind=output_binding,
    #     supersede row exists, future promote is rejected.
    #   * supersede won → no Resource row, supersede row exists,
    #     promote was rejected.
    factory = get_session_factory()
    with factory() as session:
        from src.infra.db.models import ResourceModel, OutputBindingSupersedeModel
        resource_count = session.scalar(
            select(__import__("sqlalchemy").func.count(ResourceModel.resource_id)).where(
                ResourceModel.promotion_source_ref_id == binding_id,
            )
        )
        supersede_count = session.scalar(
            select(__import__("sqlalchemy").func.count(OutputBindingSupersedeModel.supersede_id)).where(
                OutputBindingSupersedeModel.ref_id == binding_id,
            )
        )
    assert supersede_count == 1, f"exactly one supersede row expected, got {supersede_count}"
    # Either the promote committed (resource_count == 1) and its provenance is intact,
    # or the promote was rejected (resource_count == 0).  Both are consistent states.
    if resource_count == 1:
        # Linearisation: promote won.  Subsequent promote is rejected.
        with pytest.raises(ConflictError):
            resources.promote_from_source(
                owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
            )
    else:
        # Linearisation: supersede won.  The in-flight promote was rejected.
        assert results["promote"][0] == "conflict", results["promote"]


def test_p0_supersede_resolver_owner_scope_isolation(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
    owner_b: OwnerScope,
) -> None:
    """Same-owner supersede blocks the owner; foreign supersede is invisible
    but the binding's owner check still rejects tenant B."""
    target = _inline_artifact(artifacts, owner_a, {"v": 1})
    binding_id = _seed_output_binding(owner_a, target.artifact_version_id)
    factory = get_session_factory()
    with factory.begin() as session:
        session.add(OutputBindingSupersedeModel(
            supersede_id=uuid4(), owner_scope=owner_a.scoped_id,
            ref_kind="output_binding", ref_id=binding_id,
            superseded_by_ref_id=None, reason="audit",
        ))
    with pytest.raises(ConflictError):
        resources.promote_from_source(
            owner_a, PromotionSource(kind="output_binding", binding_id=binding_id), "world",
        )
    with pytest.raises(CrossOwnerError):
        resources.resolve_promotion_source(
            owner_b, PromotionSource(kind="output_binding", binding_id=binding_id),
        )


# ---------------------------------------------------------------------------
# P0 #3 — Projection rebuild does not touch canonical rows
# ---------------------------------------------------------------------------


def _artifact_snapshot(version_id: UUID) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        row = session.get(ArtifactVersionModel, version_id)
        return {
            "artifact_version_id": str(row.artifact_version_id),
            "artifact_id": str(row.artifact_id),
            "schema_id": row.schema_id,
            "schema_version": row.schema_version,
            "content_hash": row.content_hash,
            "content_uri": row.content_uri,
            "blob_uri": row.blob_uri,
            "metadata_json": dict(row.metadata_json or {}),
            "lineage_input_refs": list(row.lineage_input_refs or []),
        }


def _revision_snapshot(revision_id: UUID) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        row = session.get(ResourceRevisionModel, revision_id)
        return {
            "revision_id": str(row.revision_id),
            "resource_id": str(row.resource_id),
            "revision_number": row.revision_number,
            "content_artifact_version_id": str(row.content_artifact_version_id),
            "revision_status": (
                row.revision_status.value if hasattr(row.revision_status, "value")
                else row.revision_status
            ),
        }


def test_p0_projection_rebuild_does_not_mutate_canonical_artifact_or_revision(
    artifacts: SqlArtifactRepository,
    resources: SqlResourceRepository,
    owner_a: OwnerScope,
) -> None:
    upstream = _inline_artifact(artifacts, owner_a, {"u": True}, content_hash="upstream-p0")
    artifacts.create_version(
        owner_scope=owner_a, schema_id="p0/derived-2", schema_version=1,
        content_json={"d": "v2"}, content_hash=sha256_hex(b"v2"),
        lineage_input_refs=[{
            "source_ref": {"artifact_version_id": str(upstream.artifact_version_id)},
            "role": "input", "order": 0,
            "producer": {"run_id": str(uuid4())},
            "transformation": {"kind": "passthrough"},
            "captured_policy_refs": [],
        }],
    )
    base_art = _inline_artifact(artifacts, owner_a, {"v": "freeze-base"})
    resource = resources.create(owner_a, "world", base_art.artifact_version_id)
    draft = resources.get_draft(resource.resource_id, owner_a)
    revision = resources.freeze(resource.resource_id, owner_a, draft.draft_version)

    factory = get_session_factory()
    with factory() as session:
        canonical_artifacts = {
            str(row.artifact_version_id): _artifact_snapshot(row.artifact_version_id)
            for row in session.scalars(select(ArtifactVersionModel).where(
                ArtifactVersionModel.owner_scope == owner_a.scoped_id
            ))
        }
        canonical_revision = _revision_snapshot(revision.revision_id)
        canonical_edges = sorted(
            (e.order_index, e.role, str(e.source_ref.get("artifact_version_id")))
            for e in session.scalars(select(LineageEdgeModel))
        )

    with factory.begin() as session:
        session.query(LineageEdgeProjectionModel).delete()
        session.query(BlobReferenceIndexModel).filter(
            BlobReferenceIndexModel.owner_scope == owner_a.scoped_id
        ).delete()

    rewritten = artifacts.rebuild_lineage_projection(owner_a)
    assert rewritten >= 1

    with factory() as session:
        rebuilt_artifacts = {
            str(row.artifact_version_id): _artifact_snapshot(row.artifact_version_id)
            for row in session.scalars(select(ArtifactVersionModel).where(
                ArtifactVersionModel.owner_scope == owner_a.scoped_id
            ))
        }
        rebuilt_revision = _revision_snapshot(revision.revision_id)
        rebuilt_edges = sorted(
            (e.order_index, e.role, str(e.source_ref.get("artifact_version_id")))
            for e in session.scalars(select(LineageEdgeModel))
        )

    assert rebuilt_artifacts == canonical_artifacts, \
        "ArtifactVersion mutated across rebuild"
    assert rebuilt_revision == canonical_revision, \
        "ResourceRevision mutated across rebuild"
    assert rebuilt_edges == canonical_edges, \
        "LineageEdge mutated across rebuild"
    projections = artifacts.lineage_projection_rows(owner_a)
    assert len(projections) >= 1, "projection rebuild must repopulate the table"


# ---------------------------------------------------------------------------
# P0 #4 — Blob finalize_delete re-scans under the same transaction
# ---------------------------------------------------------------------------


def test_p0_blob_finalize_delete_rejects_late_reference(
    artifacts: SqlArtifactRepository,
    blobs: SqlBlobRepository,
    owner_a: OwnerScope,
) -> None:
    """Between ``mark_deletion_pending`` and ``finalize_delete`` a new
    ArtifactBlobRef appears (modelled as a late write to
    ArtifactBlobRefModel, since the durable route through
    ``ArtifactVersion.create_version`` is gated by the AVAILABLE
    status).  ``finalize_delete`` MUST re-scan inside the critical
    section and refuse — no silent orphaning of historical content.
    """
    from src.infra.db.models import ArtifactBlobRefModel
    payload = b"p0-blob-payload"
    blob = _upload_blob(blobs, owner_a, payload)
    blobs.assert_lifecycle_allowed(blob.blob_id, owner_a, "delete")
    blobs.mark_deletion_pending(blob.blob_id, owner_a)

    blob_art = _inline_artifact(
        artifacts, owner_a, {"v": "p0"}, content_hash=sha256_hex(payload),
    )
    factory = get_session_factory()
    with factory.begin() as session:
        session.add(ArtifactBlobRefModel(
            ref_id=uuid4(),
            artifact_version_id=blob_art.artifact_version_id,
            blob_id=blob.blob_id,
            owner_scope=owner_a.scoped_id,
            role="primary",
        ))

    with pytest.raises(ValidationError_) as exc_info:
        blobs.finalize_delete(blob.blob_id, owner_a)
    assert "仍被" in str(exc_info.value.message) or "references" in (exc_info.value.details or {}), \
        f"unexpected error: {exc_info.value!r}"

    with factory() as session:
        row = session.get(BlobModel, blob.blob_id)
        assert row is not None
        assert row.status == BlobStatus.DELETION_PENDING