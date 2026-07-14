"""Persistent encrypted credential bindings and tool invocation audit.

Revision ID: c1d2e3f4a5b6
Revises: fdc3d416ffb3
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c1d2e3f4a5b6"
down_revision = "fdc3d416ffb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_revisions", sa.Column("approval_status", sa.String(32), nullable=False, server_default="pending"))
    op.create_table("credential_bindings",
        sa.Column("binding_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_scope", sa.String(255), nullable=False),
        sa.Column("tool_revision_id", UUID(as_uuid=True), sa.ForeignKey("tool_revisions.revision_id"), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False), sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("revoked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_credential_bindings_owner_scope", "credential_bindings", ["owner_scope"])
    op.create_index("ix_credential_bindings_tool_revision_id", "credential_bindings", ["tool_revision_id"])
    op.create_table("tool_invocations",
        sa.Column("invocation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_revision_id", UUID(as_uuid=True), sa.ForeignKey("tool_revisions.revision_id"), nullable=False),
        sa.Column("credential_binding_id", UUID(as_uuid=True), sa.ForeignKey("credential_bindings.binding_id"), nullable=False),
        sa.Column("owner_scope", sa.String(255), nullable=False), sa.Column("operation_id", sa.String(255), nullable=False),
        sa.Column("input_fingerprint", sa.String(128), nullable=False), sa.Column("disclosure_manifest", sa.JSON(), nullable=False),
        sa.Column("policy_decision", sa.String(32), nullable=False), sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("result_fingerprint", sa.String(128), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_tool_invocations_owner_scope", "tool_invocations", ["owner_scope"])


def downgrade() -> None:
    op.drop_table("tool_invocations")
    op.drop_table("credential_bindings")
    op.drop_column("tool_revisions", "approval_status")
