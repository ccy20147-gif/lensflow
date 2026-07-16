"""Add promotion provenance columns to resources.

Revision ID: 5e6f7a8b9c0d
Revises: 0c5d4e3f2a1b
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "5e6f7a8b9c0d"
down_revision = "0c5d4e3f2a1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resources",
        sa.Column(
            "promotion_source_kind",
            sa.String(length=32),
            nullable=False,
            server_default="bootstrap",
        ),
    )
    op.add_column(
        "resources",
        sa.Column("promotion_source_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "resources",
        sa.Column("promotion_source_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resources", "promotion_source_artifact_version_id")
    op.drop_column("resources", "promotion_source_ref_id")
    op.drop_column("resources", "promotion_source_kind")