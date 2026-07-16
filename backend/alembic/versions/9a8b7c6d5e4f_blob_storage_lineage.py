"""Blob storage, lineage and promotion gate for Batch A (TF-OPS-003 + TF-WF-005).

Revision ID: 9a8b7c6d5e4f
Revises: f0a1b2c3d4e5
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "9a8b7c6d5e4f"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # blobs
    # ------------------------------------------------------------------
    op.create_table(
        "blobs",
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploading"),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column("durability_receipt", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("storage_key", name="uq_blobs_storage_key"),
    )
    op.create_index("ix_blobs_owner_scope", "blobs", ["owner_scope"])
    op.create_index("ix_blobs_content_hash", "blobs", ["content_hash"])

    # ------------------------------------------------------------------
    # upload_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "upload_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("blobs.blob_id"), nullable=False),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("expected_size_bytes", sa.Integer(), nullable=False),
        sa.Column("expected_content_hash", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="initiated"),
        sa.Column("part_state", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("bytes_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_scope", "idempotency_key", name="uq_upload_sessions_idempotency"),
    )
    op.create_index("ix_upload_sessions_blob_id", "upload_sessions", ["blob_id"])
    op.create_index("ix_upload_sessions_owner_scope", "upload_sessions", ["owner_scope"])

    # ------------------------------------------------------------------
    # artifact_blob_refs
    # ------------------------------------------------------------------
    op.create_table(
        "artifact_blob_refs",
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("artifact_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("blobs.blob_id"), nullable=False),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="primary"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("artifact_version_id", "blob_id", "role", name="uq_artifact_blob_refs_triplet"),
    )
    op.create_index("ix_artifact_blob_refs_artifact_version_id", "artifact_blob_refs", ["artifact_version_id"])
    op.create_index("ix_artifact_blob_refs_blob_id", "artifact_blob_refs", ["blob_id"])
    op.create_index("ix_artifact_blob_refs_owner_scope", "artifact_blob_refs", ["owner_scope"])

    # ------------------------------------------------------------------
    # blob_reference_index (projection of canonical refs)
    # ------------------------------------------------------------------
    op.create_table(
        "blob_reference_index",
        sa.Column("index_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("blobs.blob_id"), nullable=False),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("ref_kind", sa.String(length=32), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("blob_id", "ref_kind", "ref_id", name="uq_blob_reference_index"),
    )
    op.create_index("ix_blob_reference_index_blob_id", "blob_reference_index", ["blob_id"])
    op.create_index("ix_blob_reference_index_owner_scope", "blob_reference_index", ["owner_scope"])

    # ------------------------------------------------------------------
    # lineage_edges (durable first-class lineage)
    # ------------------------------------------------------------------
    op.create_table(
        "lineage_edges",
        sa.Column("edge_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("artifact_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("source_ref", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="input"),
        sa.Column("producer", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("transformation", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("captured_policy_refs", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("artifact_version_id", "order_index", name="uq_lineage_edges_version_order"),
    )
    op.create_index("ix_lineage_edges_artifact_version_id", "lineage_edges", ["artifact_version_id"])

    # ------------------------------------------------------------------
    # audit_log (minimal, durable enough for blob reference protection)
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("blobs.blob_id"), nullable=True),
        sa.Column("ref_kind", sa.String(length=64), nullable=True),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_log_owner_scope", "audit_log", ["owner_scope"])
    op.create_index("ix_audit_log_blob_id", "audit_log", ["blob_id"])

    # ------------------------------------------------------------------
    # output_binding_supersedes (promotion gate)
    # ------------------------------------------------------------------
    op.create_table(
        "output_binding_supersedes",
        sa.Column("supersede_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("ref_kind", sa.String(length=32), nullable=False),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_by_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("ref_kind", "ref_id", name="uq_output_binding_supersede_target"),
    )
    op.create_index("ix_output_binding_supersedes_owner_scope", "output_binding_supersedes", ["owner_scope"])

    # ------------------------------------------------------------------
    # artifact_versions.owner_scope explicit index (cross-owner resolution)
    # already covered by ix_artifact_versions_owner_scope in the original
    # migration; add a backstop index on (blob_uri) so the delete-check
    # remains cheap even before the projection is rebuilt.
    # ------------------------------------------------------------------
    op.create_index("ix_artifact_versions_blob_uri", "artifact_versions", ["blob_uri"])


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_blob_uri", table_name="artifact_versions")
    op.drop_index("ix_output_binding_supersedes_owner_scope", table_name="output_binding_supersedes")
    op.drop_table("output_binding_supersedes")
    op.drop_index("ix_audit_log_blob_id", table_name="audit_log")
    op.drop_index("ix_audit_log_owner_scope", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_lineage_edges_artifact_version_id", table_name="lineage_edges")
    op.drop_table("lineage_edges")
    op.drop_index("ix_blob_reference_index_owner_scope", table_name="blob_reference_index")
    op.drop_index("ix_blob_reference_index_blob_id", table_name="blob_reference_index")
    op.drop_table("blob_reference_index")
    op.drop_index("ix_artifact_blob_refs_owner_scope", table_name="artifact_blob_refs")
    op.drop_index("ix_artifact_blob_refs_blob_id", table_name="artifact_blob_refs")
    op.drop_index("ix_artifact_blob_refs_artifact_version_id", table_name="artifact_blob_refs")
    op.drop_table("artifact_blob_refs")
    op.drop_index("ix_upload_sessions_owner_scope", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_blob_id", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    op.drop_index("ix_blobs_content_hash", table_name="blobs")
    op.drop_index("ix_blobs_owner_scope", table_name="blobs")
    op.drop_table("blobs")