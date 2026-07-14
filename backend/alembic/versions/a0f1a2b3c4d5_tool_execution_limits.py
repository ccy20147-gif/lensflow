"""Persist Tool invocation reservations for run-bound execution limits."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "a0f1a2b3c4d5"
down_revision = "9e0f1a2b3c4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("reserved_cost", sa.Float(), nullable=False, server_default="0"))
    op.add_column("tool_invocations", sa.Column("actual_cost", sa.Float(), nullable=True))
    op.add_column("tool_invocations", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("tool_invocations", "reserved_cost", server_default=None)
    op.alter_column("tool_invocations", "retry_count", server_default=None)
    op.create_index("ix_tool_invocations_attempt_operation", "tool_invocations", ["node_run_attempt_id", "tool_revision_id", "operation_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_attempt_operation", table_name="tool_invocations")
    op.drop_column("tool_invocations", "retry_count")
    op.drop_column("tool_invocations", "actual_cost")
    op.drop_column("tool_invocations", "reserved_cost")
