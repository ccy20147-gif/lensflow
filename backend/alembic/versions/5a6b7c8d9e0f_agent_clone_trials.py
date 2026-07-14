"""Persist Agent clone lineage and isolated Studio trial runs.

Revision ID: 5a6b7c8d9e0f
Revises: 4f5a6b7c8d9e
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "5a6b7c8d9e0f"
down_revision = "4f5a6b7c8d9e"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("agent_definitions", sa.Column("cloned_from_agent_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_agent_definitions_cloned_from_agent_id", "agent_definitions", ["cloned_from_agent_id"])
    op.create_table("agent_trial_runs",
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_definitions.agent_id"), nullable=False),
        sa.Column("owner_scope", sa.String(255), nullable=False), sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("fixed_body", sa.JSON(), nullable=False), sa.Column("fixed_input", sa.JSON(), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_owner", sa.String(128), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_trial_runs_agent_id", "agent_trial_runs", ["agent_id"])
    op.create_index("ix_agent_trial_runs_owner_scope", "agent_trial_runs", ["owner_scope"])
    op.create_table("agent_trial_step_traces",
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_trial_runs.trial_id"), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False), sa.Column("tool_disclosures", sa.JSON(), nullable=False),
        sa.Column("failure_owner", sa.String(128), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_trial_step_traces_trial_id", "agent_trial_step_traces", ["trial_id"])

def downgrade() -> None:
    op.drop_table("agent_trial_step_traces")
    op.drop_table("agent_trial_runs")
    op.drop_index("ix_agent_definitions_cloned_from_agent_id", table_name="agent_definitions")
    op.drop_column("agent_definitions", "cloned_from_agent_id")
