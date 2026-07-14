"""Add durable, actor-attributed Human Gate decisions.

Revision ID: 2a9f8e7d6c5b
Revises: 1e4f5a6b7c8d
"""
from alembic import op
import sqlalchemy as sa


revision = "2a9f8e7d6c5b"
down_revision = "1e4f5a6b7c8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_task_decisions",
        sa.Column("decision_id", sa.UUID(), primary_key=True),
        sa.Column("task_id", sa.UUID(), sa.ForeignKey("human_tasks.task_id"), nullable=False),
        sa.Column("task_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("actor_scope", sa.String(255), nullable=False),
        sa.Column("typed_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("policy_evidence_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("idempotency_token", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", "task_version", name="uq_human_task_decision_version"),
        sa.UniqueConstraint("task_id", "idempotency_token", name="uq_human_task_decision_token"),
    )
    op.create_index("ix_human_task_decisions_task_id", "human_task_decisions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_human_task_decisions_task_id", table_name="human_task_decisions")
    op.drop_table("human_task_decisions")
