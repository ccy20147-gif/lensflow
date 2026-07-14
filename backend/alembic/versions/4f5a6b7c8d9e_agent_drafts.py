"""Persist mutable AgentDraft compare-and-swap state.

Revision ID: 4f5a6b7c8d9e
Revises: 3e4f5a6b7c8d
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "4f5a6b7c8d9e"
down_revision = "3e4f5a6b7c8d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_drafts",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_definitions.agent_id"), primary_key=True),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("base_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("agent_drafts")
