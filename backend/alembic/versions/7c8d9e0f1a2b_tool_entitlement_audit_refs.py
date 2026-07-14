"""Persist non-sensitive Tool entitlement and disclosure evidence."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "7c8d9e0f1a2b"
down_revision = "6b7c8d9e0f1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("disclosure_manifest_hash", sa.String(128), nullable=False, server_default=""))
    op.add_column("tool_invocations", sa.Column("decision_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.alter_column("tool_invocations", "disclosure_manifest_hash", server_default=None)
    op.alter_column("tool_invocations", "decision_refs", server_default=None)


def downgrade() -> None:
    op.drop_column("tool_invocations", "decision_refs")
    op.drop_column("tool_invocations", "disclosure_manifest_hash")
