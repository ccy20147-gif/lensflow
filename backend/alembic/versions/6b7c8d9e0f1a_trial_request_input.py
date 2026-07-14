"""Persist isolated Agent trial RequestInput checkpoints."""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision = "6b7c8d9e0f1a"
down_revision = "5a6b7c8d9e0f"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("agent_trial_request_inputs", sa.Column("task_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("trial_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_trial_runs.trial_id"), nullable=False), sa.Column("schema_ref", sa.String(255), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("input_schema", sa.JSON(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("task_version", sa.Integer(), nullable=False), sa.Column("answer", sa.JSON(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_agent_trial_request_inputs_trial_id", "agent_trial_request_inputs", ["trial_id"])
def downgrade() -> None:
    op.drop_table("agent_trial_request_inputs")
