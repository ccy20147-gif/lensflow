"""Persist generic business-node evidence and workflow-owned ResourceCommit.

Revision ID: ab10c3d4e5f6
Revises: fdc3d416ffb3
"""
from alembic import op
import sqlalchemy as sa


revision = "ab10c3d4e5f6"
down_revision = "fdc3d416ffb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_sets",
        sa.Column("candidate_set_id", sa.UUID(), primary_key=True),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("workflow_runs.run_id")),
        sa.Column("node_run_id", sa.UUID(), sa.ForeignKey("node_runs.node_run_id")),
        sa.Column("candidate_refs", sa.JSON(), nullable=False),
        sa.Column("failed_candidates", sa.JSON(), nullable=False),
        sa.Column("cost_allocation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_candidate_sets_owner_scope", "candidate_sets", ["owner_scope"])
    op.create_index("ix_candidate_sets_run_id", "candidate_sets", ["run_id"])
    op.create_table(
        "selection_records",
        sa.Column("selection_id", sa.UUID(), primary_key=True),
        sa.Column("candidate_set_id", sa.UUID(), sa.ForeignKey("candidate_sets.candidate_set_id"), nullable=False),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("ranking", sa.JSON(), nullable=False),
        sa.Column("selected_refs", sa.JSON(), nullable=False),
        sa.Column("actor_or_model", sa.String(length=255), nullable=False),
        sa.Column("rubric_revision", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_selection_records_candidate_set_id", "selection_records", ["candidate_set_id"])
    op.create_index("ix_selection_records_owner_scope", "selection_records", ["owner_scope"])
    op.create_table(
        "resource_commits",
        sa.Column("commit_id", sa.UUID(), primary_key=True),
        sa.Column("task_id", sa.UUID(), sa.ForeignKey("human_tasks.task_id"), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("revision_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=255), nullable=False),
        sa.Column("owner_scope", sa.String(length=255), nullable=False),
        sa.Column("source_artifact_version_id", sa.UUID(), sa.ForeignKey("artifact_versions.artifact_version_id"), nullable=False),
        sa.Column("expected_draft_version", sa.Integer(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("resource_id", "revision_number", name="uq_resource_commits_revision"),
    )
    op.create_index("ix_resource_commits_task_id", "resource_commits", ["task_id"])
    op.create_index("ix_resource_commits_resource_id", "resource_commits", ["resource_id"])
    op.create_index("ix_resource_commits_owner_scope", "resource_commits", ["owner_scope"])


def downgrade() -> None:
    op.drop_index("ix_resource_commits_owner_scope", table_name="resource_commits")
    op.drop_index("ix_resource_commits_resource_id", table_name="resource_commits")
    op.drop_index("ix_resource_commits_task_id", table_name="resource_commits")
    op.drop_table("resource_commits")
    op.drop_index("ix_selection_records_owner_scope", table_name="selection_records")
    op.drop_index("ix_selection_records_candidate_set_id", table_name="selection_records")
    op.drop_table("selection_records")
    op.drop_index("ix_candidate_sets_run_id", table_name="candidate_sets")
    op.drop_index("ix_candidate_sets_owner_scope", table_name="candidate_sets")
    op.drop_table("candidate_sets")
