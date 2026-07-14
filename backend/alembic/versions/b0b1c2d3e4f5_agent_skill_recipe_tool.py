"""TF-ASR-001: Add agent/skill/recipe/tool persistence tables.

Revision ID: b0b1c2d3e4f5
Revises: a1b2c3d4e5f6
Create Date: 2026-07-13
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "b0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agent_definitions ---
    op.create_table(
        "agent_definitions",
        sa.Column("agent_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_kind", sa.String(64), nullable=False),
        sa.Column("owner_scope", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_definitions_owner_scope", "agent_definitions", ["owner_scope"])

    # --- agent_revisions ---
    op.create_table(
        "agent_revisions",
        sa.Column("revision_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agent_definitions.agent_id"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_revisions_agent_id", "agent_revisions", ["agent_id"])
    op.create_unique_constraint("uq_agent_revisions_number", "agent_revisions", ["agent_id", "revision_number"])

    # --- skill_contents ---
    op.create_table(
        "skill_contents",
        sa.Column("skill_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_scope", sa.String(255), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_skill_contents_owner_scope", "skill_contents", ["owner_scope"])

    # --- skill_assembly_plans ---
    op.create_table(
        "skill_assembly_plans",
        sa.Column("plan_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("skill_contents.skill_id"), nullable=False),
        sa.Column("agent_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_skill_assembly_plans_skill_id", "skill_assembly_plans", ["skill_id"])

    # --- tool_definitions ---
    op.create_table(
        "tool_definitions",
        sa.Column("tool_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_scope", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("approval_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tool_definitions_owner_scope", "tool_definitions", ["owner_scope"])

    # --- tool_revisions ---
    op.create_table(
        "tool_revisions",
        sa.Column("revision_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_id", UUID(as_uuid=True), sa.ForeignKey("tool_definitions.tool_id"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_tool_revisions_tool_id", "tool_revisions", ["tool_id"])
    op.create_unique_constraint("uq_tool_revisions_number", "tool_revisions", ["tool_id", "revision_number"])

    # --- media_recipe_definitions ---
    op.create_table(
        "media_recipe_definitions",
        sa.Column("recipe_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_scope", sa.String(255), nullable=False),
        sa.Column("recipe_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_recipe_definitions_owner_scope", "media_recipe_definitions", ["owner_scope"])

    # --- media_recipe_revisions ---
    op.create_table(
        "media_recipe_revisions",
        sa.Column("revision_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("recipe_id", UUID(as_uuid=True), sa.ForeignKey("media_recipe_definitions.recipe_id"), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("base_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_recipe_revisions_recipe_id", "media_recipe_revisions", ["recipe_id"])
    op.create_unique_constraint("uq_media_recipe_revisions_number", "media_recipe_revisions", ["recipe_id", "revision_number"])


def downgrade() -> None:
    op.drop_table("media_recipe_revisions")
    op.drop_table("media_recipe_definitions")
    op.drop_table("tool_revisions")
    op.drop_table("tool_definitions")
    op.drop_table("skill_assembly_plans")
    op.drop_table("skill_contents")
    op.drop_table("agent_revisions")
    op.drop_table("agent_definitions")
