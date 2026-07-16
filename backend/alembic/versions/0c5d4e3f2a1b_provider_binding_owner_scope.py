"""Add owner_scope to ProviderOutputBindingModel for promotion gate.

Revision ID: 0c5d4e3f2a1b
Revises: 9a8b7c6d5e4f
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0c5d4e3f2a1b"
down_revision = "9a8b7c6d5e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provider_output_bindings",
        sa.Column("owner_scope", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_provider_output_bindings_owner_scope",
        "provider_output_bindings",
        ["owner_scope"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_output_bindings_owner_scope", table_name="provider_output_bindings")
    op.drop_column("provider_output_bindings", "owner_scope")