"""Persist the durable runtime identity of an isolated Agent Studio trial."""

from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_trial_runs", sa.Column("runtime_run_id", sa.UUID(), nullable=True))
    op.add_column("agent_trial_runs", sa.Column("runtime_node_run_id", sa.UUID(), nullable=True))
    op.add_column("agent_trial_runs", sa.Column("runtime_attempt_id", sa.UUID(), nullable=True))
    op.add_column("agent_trial_runs", sa.Column("runtime_agent_revision_id", sa.UUID(), nullable=True))
    op.create_index("ix_agent_trial_runs_runtime_run_id", "agent_trial_runs", ["runtime_run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_trial_runs_runtime_run_id", table_name="agent_trial_runs")
    op.drop_column("agent_trial_runs", "runtime_agent_revision_id")
    op.drop_column("agent_trial_runs", "runtime_attempt_id")
    op.drop_column("agent_trial_runs", "runtime_node_run_id")
    op.drop_column("agent_trial_runs", "runtime_run_id")
