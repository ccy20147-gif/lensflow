"""Make bounded control-flow execution durable.

Revision ID: 0d3c4b5a6e7f
Revises: fdc3d416ffb3
"""
from alembic import op
import sqlalchemy as sa

revision = "0d3c4b5a6e7f"
down_revision = "fdc3d416ffb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "map_item_runs",
        sa.Column("item_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_map_item_runs_order",
        "map_item_runs",
        ["run_id", "node_instance_id", "item_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_map_item_runs_order", table_name="map_item_runs")
    op.drop_column("map_item_runs", "item_index")
