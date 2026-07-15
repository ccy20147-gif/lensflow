"""Persist stale diagnostics for unfixed resource drafts.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource_drafts", sa.Column("stale_reason", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("resource_drafts", "stale_reason")
