"""Persist fenced Fold accumulator checkpoints.

Revision ID: ee50f6a7b8c9
Revises: dd40c3d4e5f6
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ee50f6a7b8c9"
down_revision = "dd40c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fold_checkpoints",
        sa.Column("checkpoint_id", sa.UUID(), primary_key=True),
        sa.Column("for_each_id", sa.UUID(), sa.ForeignKey("for_each_runs.for_each_id"), nullable=False, index=True),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("execution_epoch", sa.Integer(), nullable=False),
        sa.Column("accumulator", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("for_each_id", "item_index", "execution_epoch", name="uq_fold_checkpoint_epoch"),
    )


def downgrade() -> None:
    op.drop_table("fold_checkpoints")
