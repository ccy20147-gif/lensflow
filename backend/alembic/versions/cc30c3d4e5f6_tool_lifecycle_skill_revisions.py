"""Persist ToolInvocation lifecycle and immutable Skill revisions.

Revision ID: cc30c3d4e5f6
Revises: 5d9e3f7a1b2c
"""
from alembic import op
import sqlalchemy as sa

revision = "cc30c3d4e5f6"
down_revision = "5d9e3f7a1b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("idempotency_key", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("tool_invocations", sa.Column("status", sa.String(length=32), nullable=False, server_default="authorized"))
    op.add_column("tool_invocations", sa.Column("cancellation_requested_at", sa.DateTime(), nullable=True))
    op.add_column("tool_invocations", sa.Column("reconciled_at", sa.DateTime(), nullable=True))
    op.add_column("tool_invocations", sa.Column("late_result_quarantined", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("skill_revisions", sa.Column("revision_id", sa.UUID(), primary_key=True), sa.Column("skill_id", sa.UUID(), sa.ForeignKey("skill_contents.skill_id"), nullable=False), sa.Column("revision_number", sa.Integer(), nullable=False), sa.Column("body", sa.JSON(), nullable=False), sa.Column("content_hash", sa.String(length=64), nullable=False), sa.Column("status", sa.String(length=32), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"))
    op.create_index("ix_skill_revisions_skill_id", "skill_revisions", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_revisions_skill_id", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_column("tool_invocations", "late_result_quarantined")
    op.drop_column("tool_invocations", "reconciled_at")
    op.drop_column("tool_invocations", "cancellation_requested_at")
    op.drop_column("tool_invocations", "status")
    op.drop_column("tool_invocations", "idempotency_key")
