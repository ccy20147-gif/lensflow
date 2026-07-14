"""Persist immutable World-to-OC elevation provenance.

Revision ID: 1b2c3d4e5f6a
Revises: f2a4b6c8d0e1
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "1b2c3d4e5f6a"
down_revision = "f2a4b6c8d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("source_world_revision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("resources", sa.Column("source_local_id", sa.String(length=255), nullable=True))
    op.add_column("resources", sa.Column("elevation_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_resources_source_world_revision_id", "resources", ["source_world_revision_id"])
    op.create_unique_constraint("uq_resources_elevation_event_id", "resources", ["elevation_event_id"])
    op.add_column("resource_revisions", sa.Column("source_world_revision_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("resource_revisions", sa.Column("source_local_id", sa.String(length=255), nullable=True))
    op.add_column("resource_revisions", sa.Column("elevation_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_resource_revisions_source_world_revision_id", "resource_revisions", ["source_world_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_resource_revisions_source_world_revision_id", table_name="resource_revisions")
    op.drop_column("resource_revisions", "elevation_event_id")
    op.drop_column("resource_revisions", "source_local_id")
    op.drop_column("resource_revisions", "source_world_revision_id")
    op.drop_constraint("uq_resources_elevation_event_id", "resources", type_="unique")
    op.drop_index("ix_resources_source_world_revision_id", table_name="resources")
    op.drop_column("resources", "elevation_event_id")
    op.drop_column("resources", "source_local_id")
    op.drop_column("resources", "source_world_revision_id")
