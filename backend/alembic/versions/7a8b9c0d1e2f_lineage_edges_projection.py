"""Add lineage_edges_projections table for rebuildable projection.

Revision ID: 7a8b9c0d1e2f
Revises: 5e6f7a8b9c0d
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "7a8b9c0d1e2f"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lineage_edges_projections",
        sa.Column("projection_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("artifact_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("source_ref", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="input"),
        sa.Column("producer", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("transformation", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("captured_policy_refs", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("rebuilt_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("artifact_version_id", "order_index", name="uq_lineage_edges_projections_version_order"),
    )
    op.create_index(
        "ix_lineage_edges_projections_artifact_version_id",
        "lineage_edges_projections",
        ["artifact_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_lineage_edges_projections_artifact_version_id", table_name="lineage_edges_projections")
    op.drop_table("lineage_edges_projections")