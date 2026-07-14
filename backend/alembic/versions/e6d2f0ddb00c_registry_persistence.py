"""Persist registry definitions, converters, and snapshots.

Revision ID: e6d2f0ddb00c
Revises: 8d24f0a0b1c2
Create Date: 2026-07-13

This migration replaces the in-memory RegistryService with PostgreSQL-backed
tables so that active definitions, converters, and registry snapshots survive
process restarts.  Snapshots are stored with their schema_hash so the
compiler can verify immutability at load time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6d2f0ddb00c"
down_revision: Union[str, Sequence[str], None] = "8d24f0a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_definitions",
        sa.Column("revision_id", sa.UUID(), primary_key=True),
        sa.Column("node_type_id", sa.String(length=255), nullable=False),
        sa.Column("semantic_version", sa.String(length=64), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum("DRAFT", "ACTIVE", "RETIRED", name="nodedefinitionstatus"),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_node_definitions_node_type_id", "node_definitions", ["node_type_id"]
    )
    op.create_unique_constraint(
        "uq_node_definitions_type_version",
        "node_definitions",
        ["node_type_id", "semantic_version"],
    )

    op.create_table(
        "converter_revisions",
        sa.Column("converter_id", sa.UUID(), primary_key=True),
        sa.Column("from_schema_id", sa.String(length=255), nullable=False),
        sa.Column("from_schema_version", sa.Integer(), nullable=False),
        sa.Column("to_schema_id", sa.String(length=255), nullable=False),
        sa.Column("to_schema_version", sa.Integer(), nullable=False),
        sa.Column("executor_digest", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_converter_revisions_four_tuple",
        "converter_revisions",
        ["from_schema_id", "from_schema_version", "to_schema_id", "to_schema_version"],
    )

    op.create_table(
        "registry_snapshots",
        sa.Column("snapshot_id", sa.UUID(), primary_key=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=False),
        sa.Column("node_definitions", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("converter_revisions", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_registry_snapshots_schema_hash", "registry_snapshots", ["schema_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_registry_snapshots_schema_hash", table_name="registry_snapshots")
    op.drop_table("registry_snapshots")
    op.drop_table("converter_revisions")
    op.drop_index("ix_node_definitions_node_type_id", table_name="node_definitions")
    op.drop_table("node_definitions")
    op.execute("DROP TYPE IF EXISTS nodedefinitionstatus CASCADE")