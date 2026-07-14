"""Persist compiled plans and canonical resources/grant snapshots.

Revision ID: 6f1a2b3c4d5e
Revises: 5d9e3f7a1b2c
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "6f1a2b3c4d5e"
down_revision = "5d9e3f7a1b2c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_templates", sa.Column("owner_scope", sa.String(255), nullable=False, server_default=""))
    op.create_index("ix_workflow_templates_owner_scope", "workflow_templates", ["owner_scope"])
    op.create_table("compiled_execution_plans",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workflow_revisions.revision_id"), nullable=False),
        sa.Column("registry_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("plan_hash", sa.String(128), nullable=False),
        sa.Column("compiler_version", sa.String(64), nullable=False), sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_compiled_execution_plans_workflow_revision_id", "compiled_execution_plans", ["workflow_revision_id"])
    op.create_table("resources", sa.Column("resource_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("resource_type", sa.String(128), nullable=False), sa.Column("owner_scope", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_resources_owner_scope", "resources", ["owner_scope"])
    op.create_table("resource_drafts", sa.Column("resource_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resources.resource_id"), primary_key=True), sa.Column("draft_version", sa.Integer(), nullable=False), sa.Column("base_revision_id", postgresql.UUID(as_uuid=True)), sa.Column("content_artifact_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    revision_status = postgresql.ENUM("DRAFT", "ACTIVE", "RETIRED", name="revisionstatus", create_type=False)
    op.create_table("resource_revisions", sa.Column("revision_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("resource_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resources.resource_id"), nullable=False), sa.Column("revision_number", sa.Integer(), nullable=False), sa.Column("content_artifact_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False), sa.Column("revision_status", revision_status, nullable=False), sa.Column("created_from_artifact_version_id", postgresql.UUID(as_uuid=True)), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("resource_id", "revision_number", name="uq_resource_revision_number"))
    op.create_index("ix_resource_revisions_resource_id", "resource_revisions", ["resource_id"])
    op.create_table("resource_grant_snapshots", sa.Column("grant_snapshot_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("resource_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resource_revisions.revision_id"), nullable=False), sa.Column("grantee_scope", sa.String(255), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_resource_grant_snapshots_resource_revision_id", "resource_grant_snapshots", ["resource_revision_id"])
    op.create_index("ix_resource_grant_snapshots_grantee_scope", "resource_grant_snapshots", ["grantee_scope"])


def downgrade() -> None:
    op.drop_index("ix_workflow_templates_owner_scope", table_name="workflow_templates")
    op.drop_column("workflow_templates", "owner_scope")
    op.drop_table("resource_grant_snapshots")
    op.drop_table("resource_revisions")
    op.drop_table("resource_drafts")
    op.drop_table("resources")
    op.drop_table("compiled_execution_plans")
