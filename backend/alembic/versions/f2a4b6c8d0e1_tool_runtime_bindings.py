"""Bind ToolInvocation lifecycle to runtime attempts and typed outputs.

Revision ID: f2a4b6c8d0e1
Revises: ee50f6a7b8c9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2a4b6c8d0e1"
down_revision = "ee50f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("node_run_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("tool_invocations", sa.Column("output_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tool_invocation_attempt", "tool_invocations", "node_run_attempts", ["node_run_attempt_id"], ["attempt_id"])
    op.create_foreign_key("fk_tool_invocation_output", "tool_invocations", "artifact_versions", ["output_artifact_version_id"], ["artifact_version_id"])
    op.create_index("ix_tool_invocations_attempt", "tool_invocations", ["node_run_attempt_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_attempt", table_name="tool_invocations")
    op.drop_constraint("fk_tool_invocation_output", "tool_invocations", type_="foreignkey")
    op.drop_constraint("fk_tool_invocation_attempt", "tool_invocations", type_="foreignkey")
    op.drop_column("tool_invocations", "output_artifact_version_id")
    op.drop_column("tool_invocations", "node_run_attempt_id")
