"""Store immutable workflow revision content.

Revision ID: 7a38d2e8d1f4
Revises: 471fc0c910c9
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a38d2e8d1f4"
down_revision: Union[str, Sequence[str], None] = "471fc0c910c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workflow_revisions",
        sa.Column("graph", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "workflow_revisions",
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "workflow_revisions",
        sa.Column("layout", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.alter_column("workflow_revisions", "graph", server_default=None)
    op.alter_column("workflow_revisions", "config", server_default=None)
    op.alter_column("workflow_revisions", "layout", server_default=None)


def downgrade() -> None:
    op.drop_column("workflow_revisions", "layout")
    op.drop_column("workflow_revisions", "config")
    op.drop_column("workflow_revisions", "graph")
