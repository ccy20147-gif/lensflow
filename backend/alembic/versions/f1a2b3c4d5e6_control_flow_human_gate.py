"""Persist control flow (Condition/Join/MapItem/ForEach/Subworkflow) state.

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-07-13

Control flow tables for Condition, Join, MapItem, ForEach, and Subworkflow
nodes — all PostgreSQL-backed.  Map/Fold/Subworkflow are configuration-only
stubs and are not wired into execution yet.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types used by control flow tables
    condition_operator = postgresql.ENUM(
        "eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "exists",
        name="conditionoperator", create_type=False,
    )
    join_strategy = postgresql.ENUM(
        "and", "or", "xor", "sequential",
        name="joinstrategy", create_type=False,
    )
    for_each_mode = postgresql.ENUM(
        "sequential", "parallel", "batch",
        name="foreachmode", create_type=False,
    )
    map_item_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", "skipped",
        name="mapitemstatus", create_type=False,
    )
    condition_operator.create(op.get_bind(), checkfirst=True)
    join_strategy.create(op.get_bind(), checkfirst=True)
    for_each_mode.create(op.get_bind(), checkfirst=True)
    map_item_status.create(op.get_bind(), checkfirst=True)

    # Condition nodes
    op.create_table(
        "conditions",
        sa.Column("condition_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("node_instance_id", sa.String(255), nullable=False),
        sa.Column("operator", condition_operator, nullable=False),
        sa.Column("threshold", sa.JSON(), nullable=True),
        sa.Column("value_path", sa.String(255), nullable=True),
        sa.Column("expression", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("result", sa.Boolean(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "node_instance_id", name="uq_conditions_run_node"),
    )

    # Join nodes
    op.create_table(
        "joins",
        sa.Column("join_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("node_instance_id", sa.String(255), nullable=False),
        sa.Column("strategy", join_strategy, nullable=False),
        sa.Column("source_node_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "node_instance_id", name="uq_joins_run_node"),
    )

    # MapItem runs (per-item ForEach state)
    op.create_table(
        "map_item_runs",
        sa.Column("map_item_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("node_instance_id", sa.String(255), nullable=False),
        sa.Column("item_key", sa.String(255), nullable=False),
        sa.Column("item_value", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", map_item_status, nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("run_id", "node_instance_id", "item_key", name="uq_map_items_run_node_key"),
    )

    # ForEach runs (stub — config storage only)
    op.create_table(
        "for_each_runs",
        sa.Column("for_each_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("node_instance_id", sa.String(255), nullable=False),
        sa.Column("mode", for_each_mode, nullable=False, server_default="sequential"),
        sa.Column("collection_ref", sa.String(255), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "node_instance_id", name="uq_for_each_run_node"),
    )

    # Subworkflow runs (stub — config storage only)
    op.create_table(
        "subworkflows",
        sa.Column("subworkflow_id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id"), nullable=False, index=True),
        sa.Column("node_instance_id", sa.String(255), nullable=False),
        sa.Column("child_run_id", sa.UUID(), nullable=True),
        sa.Column("parent_node_instance_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "node_instance_id", name="uq_subworkflows_run_node"),
    )


def downgrade() -> None:
    op.drop_table("subworkflows")
    op.drop_table("for_each_runs")
    op.drop_table("map_item_runs")
    op.drop_table("joins")
    op.drop_table("conditions")
    postgresql.ENUM(name="mapitemstatus").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="foreachmode").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="joinstrategy").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="conditionoperator").drop(op.get_bind(), checkfirst=True)
