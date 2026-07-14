"""Alembic migration: extend artifact_versions with full ArtifactVersion schema.

Adds the columns needed by SqlArtifactRepository so we can replay
ArtifactVersion / ArtifactRef reads and writes from PostgreSQL.
"""
"""TF-WF-005: Extend artifact_versions with full ArtifactVersion schema.

Revision ID: a1b2c3d4e5f6
Revises: e6d2f0ddb00c
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e6d2f0ddb00c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "artifact_versions",
        sa.Column("artifact_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("schema_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("schema_version", sa.Integer(), nullable=True, server_default="1"),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("content_uri", sa.Text(), nullable=True, server_default=""),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("content_json", sa.JSON(), nullable=True, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("created_by_run_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("lineage_input_refs", sa.JSON(), nullable=True, server_default=sa.text("'[]'::json")),
    )

    # Backfill artifact_id from artifact_version_id for legacy rows.
    op.execute("UPDATE artifact_versions SET artifact_id = artifact_version_id WHERE artifact_id IS NULL")
    op.execute("UPDATE artifact_versions SET schema_id = 'unknown' WHERE schema_id IS NULL")

    # Keep artifact_id nullable so legacy code paths that omit it continue
    # to work; the SqlArtifactRepository always sets it for new rows.

    op.create_index(
        "ix_artifact_versions_artifact_id",
        "artifact_versions",
        ["artifact_id"],
    )
    op.create_index(
        "ix_artifact_versions_schema_id",
        "artifact_versions",
        ["schema_id"],
    )
    op.create_index(
        "ix_artifact_versions_created_by_run",
        "artifact_versions",
        ["created_by_run_id"],
    )
    op.create_index(
        "ix_artifact_versions_owner_scope",
        "artifact_versions",
        ["owner_scope"],
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_owner_scope", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_created_by_run", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_schema_id", table_name="artifact_versions")
    op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions")
    op.drop_column("artifact_versions", "lineage_input_refs")
    op.drop_column("artifact_versions", "created_by_run_id")
    op.drop_column("artifact_versions", "content_json")
    op.drop_column("artifact_versions", "content_uri")
    op.drop_column("artifact_versions", "schema_version")
    op.drop_column("artifact_versions", "schema_id")
    op.drop_column("artifact_versions", "artifact_id")