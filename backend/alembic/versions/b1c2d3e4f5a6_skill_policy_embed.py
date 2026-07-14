"""Persist Skill moderation state and authorized package embeds."""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
revision = "b1c2d3e4f5a6"
down_revision = "a0f1a2b3c4d5"
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table("skill_policy_states", sa.Column("revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skill_revisions.revision_id"), primary_key=True), sa.Column("state", sa.String(32), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_table("skill_package_embeds", sa.Column("embed_id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("skill_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skill_revisions.revision_id"), nullable=False), sa.Column("installer_scope", sa.String(255), nullable=False), sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("grant_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_skill_package_embeds_skill_revision_id", "skill_package_embeds", ["skill_revision_id"])
    op.create_index("ix_skill_package_embeds_installer_scope", "skill_package_embeds", ["installer_scope"])
def downgrade() -> None:
    op.drop_table("skill_package_embeds")
    op.drop_table("skill_policy_states")
