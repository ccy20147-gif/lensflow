"""Persist runtime execution state and transactional outbox.

Revision ID: 8d24f0a0b1c2
Revises: 7a38d2e8d1f4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "8d24f0a0b1c2"
down_revision: Union[str, Sequence[str], None] = "7a38d2e8d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    node_status = postgresql.ENUM("PENDING", "READY", "RUNNING", "WAITING_USER", "COMPLETED", "FAILED", "CANCELLED", "SKIPPED", name="noderunstatus", create_type=False)
    human_status = postgresql.ENUM("PENDING", "WAITING", "SUBMITTED", "ACCEPTED", "REJECTED", "ESCALATED", "EXPIRED", "CANCELLED", name="humantaskstatus", create_type=False)
    node_status.create(op.get_bind(), checkfirst=True)
    human_status.create(op.get_bind(), checkfirst=True)
    op.add_column("node_runs", sa.Column("status", node_status, nullable=False, server_default="PENDING"))
    op.add_column("node_run_attempts", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("node_run_attempts", sa.Column("fixed_input", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_table(
        "artifact_versions",
        sa.Column("artifact_version_id", sa.UUID(), primary_key=True),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("blob_uri", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "provider_invocation_attempts",
        sa.Column("provider_attempt_id", sa.UUID(), primary_key=True),
        sa.Column("node_run_attempt_id", sa.UUID(), sa.ForeignKey("node_run_attempts.attempt_id"), nullable=False),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("request_body_hash", sa.String(length=128), nullable=False),
        sa.Column("status", postgresql.ENUM("PENDING", "LEASED", "RUNNING", "WAITING_EXTERNAL", "COMPLETED", "FAILED", "CANCELLED", "SUPERSEDED", "UNKNOWN", name="attemptstatus", create_type=False), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_provider_invocation_attempts_attempt", "provider_invocation_attempts", ["node_run_attempt_id"])
    op.create_table(
        "provider_invocation_records",
        sa.Column("record_id", sa.UUID(), primary_key=True),
        sa.Column("provider_attempt_id", sa.UUID(), sa.ForeignKey("provider_invocation_attempts.provider_attempt_id"), nullable=False, unique=True),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_body_hash", sa.String(length=128), nullable=False),
        sa.Column("response_fingerprint", sa.String(length=255), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actual_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "provider_output_bindings",
        sa.Column("binding_id", sa.UUID(), primary_key=True),
        sa.Column("record_id", sa.UUID(), sa.ForeignKey("provider_invocation_records.record_id"), nullable=False),
        sa.Column("output_artifact_version_id", sa.UUID(), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False),
        sa.Column("output_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_label", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index("ix_provider_output_bindings_record", "provider_output_bindings", ["record_id"])
    op.create_table(
        "workflow_task_bindings",
        sa.Column("binding_id", sa.UUID(), primary_key=True),
        sa.Column("node_run_attempt_id", sa.UUID(), sa.ForeignKey("node_run_attempts.attempt_id"), nullable=False),
        sa.Column("provider_attempt_id", sa.UUID(), sa.ForeignKey("provider_invocation_attempts.provider_attempt_id"), nullable=False),
        sa.Column("provider_task_id", sa.String(length=255), nullable=False, unique=True),
        sa.Column("task_status", sa.String(length=64), nullable=False, server_default="pending"),
    )
    op.create_table(
        "human_tasks",
        sa.Column("task_id", sa.UUID(), primary_key=True),
        sa.Column("task_kind", sa.String(length=64), nullable=False),
        sa.Column("owner_layer", sa.String(length=64), nullable=False),
        sa.Column("owner_revision_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False),
        sa.Column("node_run_id", sa.UUID(), sa.ForeignKey("node_runs.node_run_id"), nullable=False),
        sa.Column("attempt_id", sa.UUID(), sa.ForeignKey("node_run_attempts.attempt_id"), nullable=False),
        sa.Column("input_snapshot_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("assignee_scope", sa.String(length=255), nullable=True),
        sa.Column("policy_strength", sa.String(length=64), nullable=False),
        sa.Column("schema_ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("timeout_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", human_status, nullable=False, server_default="PENDING"),
        sa.Column("task_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("human_tasks")
    op.drop_table("workflow_task_bindings")
    op.drop_index("ix_provider_output_bindings_record", table_name="provider_output_bindings")
    op.drop_table("provider_output_bindings")
    op.drop_table("provider_invocation_records")
    op.drop_index("ix_provider_invocation_attempts_attempt", table_name="provider_invocation_attempts")
    op.drop_table("provider_invocation_attempts")
    op.drop_table("artifact_versions")
    op.drop_column("node_run_attempts", "fixed_input")
    op.drop_column("node_run_attempts", "lease_expires_at")
    op.drop_column("node_runs", "status")
    op.execute("DROP TYPE IF EXISTS noderunstatus CASCADE")
    op.execute("DROP TYPE IF EXISTS humantaskstatus CASCADE")
