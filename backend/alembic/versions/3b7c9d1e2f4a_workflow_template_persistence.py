"""Persist pinned workflow templates and their instantiation lineage.

Revision ID: 3b7c9d1e2f4a
Revises: 2a9f8e7d6c5b
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "3b7c9d1e2f4a"
down_revision = "2a9f8e7d6c5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("template_id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("workflow_revision_id", sa.UUID(), sa.ForeignKey("workflow_revisions.revision_id"), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("parameter_schema", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("default_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="private"),
        sa.Column("provenance", sa.String(64), nullable=False, server_default="platform"),
        sa.Column("revision_status", postgresql.ENUM("DRAFT", "ACTIVE", "RETIRED", name="revisionstatus", create_type=False), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "workflow_template_instances",
        sa.Column("instance_id", sa.UUID(), primary_key=True),
        sa.Column("template_id", sa.UUID(), sa.ForeignKey("workflow_templates.template_id"), nullable=False),
        sa.Column("template_revision_id", sa.UUID(), sa.ForeignKey("workflow_revisions.revision_id"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("workflow_id", sa.UUID(), sa.ForeignKey("workflows.workflow_id"), nullable=False, unique=True),
        sa.Column("dependency_resolution", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("replacement_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("attribution_manifest", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workflow_template_instances_template_id", "workflow_template_instances", ["template_id"])
    op.create_index("ix_workflow_template_instances_project_id", "workflow_template_instances", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_template_instances_project_id", table_name="workflow_template_instances")
    op.drop_index("ix_workflow_template_instances_template_id", table_name="workflow_template_instances")
    op.drop_table("workflow_template_instances")
    op.drop_table("workflow_templates")
