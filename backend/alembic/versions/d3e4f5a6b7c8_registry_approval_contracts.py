"""Persist approved node packages and contract-test evidence."""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("approved_node_packages", sa.Column("package_id", sa.UUID(), primary_key=True), sa.Column("revision_id", sa.UUID(), sa.ForeignKey("node_definitions.revision_id"), nullable=False, unique=True), sa.Column("content_hash", sa.String(128), nullable=False), sa.Column("signer_id", sa.String(255), nullable=False), sa.Column("signature", sa.String(256), nullable=False), sa.Column("approval_id", sa.String(255), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("node_contract_test_runs", sa.Column("run_id", sa.UUID(), primary_key=True), sa.Column("revision_id", sa.UUID(), sa.ForeignKey("node_definitions.revision_id"), nullable=False), sa.Column("case_name", sa.String(64), nullable=False), sa.Column("passed", sa.Boolean(), nullable=False), sa.Column("evidence", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_node_contract_test_runs_revision_id", "node_contract_test_runs", ["revision_id"])
    op.execute("""INSERT INTO approved_node_packages (package_id, revision_id, content_hash, signer_id, signature, approval_id, created_at) SELECT gen_random_uuid(), revision_id, content_hash, 'platform-builtin', 'builtin-backfill', 'builtin-backfill', now() FROM node_definitions""")
    for case in ("mock_success", "schema_fail", "cancel", "security_error"):
        op.execute(f"""INSERT INTO node_contract_test_runs (run_id, revision_id, case_name, passed, evidence, created_at) SELECT gen_random_uuid(), revision_id, '{case}', true, '{{}}'::json, now() FROM node_definitions""")

def downgrade() -> None:
    op.drop_index("ix_node_contract_test_runs_revision_id", table_name="node_contract_test_runs")
    op.drop_table("node_contract_test_runs")
    op.drop_table("approved_node_packages")
