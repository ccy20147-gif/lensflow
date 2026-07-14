"""Fence Tool external submission with durable leases and idempotency."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "8d9e0f1a2b3c"
down_revision = "7c8d9e0f1a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("dispatch_lease_owner", sa.String(255), nullable=True))
    op.add_column("tool_invocations", sa.Column("dispatch_lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("tool_invocations", sa.Column("external_submission_started_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tool_invocations_idempotency", "tool_invocations", ["owner_scope", "idempotency_key"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_idempotency", table_name="tool_invocations")
    op.drop_column("tool_invocations", "external_submission_started_at")
    op.drop_column("tool_invocations", "dispatch_lease_expires_at")
    op.drop_column("tool_invocations", "dispatch_lease_owner")
