"""Persist the exact immutable content hash used for World-to-OC promotion.

Revision ID: 2c7d8e9f0a1b
Revises: 1b2c3d4e5f6a
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2c7d8e9f0a1b"
down_revision = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("source_content_hash", sa.String(length=128), nullable=True))
    op.add_column("resource_revisions", sa.Column("source_content_hash", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("resource_revisions", "source_content_hash")
    op.drop_column("resources", "source_content_hash")
