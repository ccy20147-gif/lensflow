"""Make resource grants action-scoped and revocable.

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2c3d4e5f6a7b"
down_revision = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A pre-capability grant has no evidence of execution or redistribution;
    # preserve it as reference-only. New grants must declare every action.
    op.add_column(
        "resource_grant_snapshots",
        sa.Column(
            "capability_actions", sa.JSON(), nullable=False,
            server_default=sa.text("'[\"reference\"]'::json"),
        ),
    )
    op.add_column("resource_grant_snapshots", sa.Column("revoked_at", sa.DateTime(), nullable=True))
    op.alter_column("resource_grant_snapshots", "capability_actions", server_default=None)


def downgrade() -> None:
    op.drop_column("resource_grant_snapshots", "revoked_at")
    op.drop_column("resource_grant_snapshots", "capability_actions")
