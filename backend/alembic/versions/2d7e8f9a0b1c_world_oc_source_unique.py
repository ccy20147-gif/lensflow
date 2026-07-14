"""Reject silent duplicate World OC promotions.

Revision ID: 2d7e8f9a0b1c
Revises: 2c7d8e9f0a1b
"""
from __future__ import annotations

from alembic import op


revision = "2d7e8f9a0b1c"
down_revision = "2c7d8e9f0a1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_resources_world_oc_source",
        "resources",
        ["source_world_revision_id", "source_local_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_resources_world_oc_source", "resources", type_="unique")
