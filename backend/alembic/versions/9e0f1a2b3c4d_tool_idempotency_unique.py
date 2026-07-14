"""Make non-empty Tool invocation idempotency keys durable and unique."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "9e0f1a2b3c4d"
down_revision = "8d9e0f1a2b3c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_tool_invocations_owner_idempotency",
        "tool_invocations",
        ["owner_scope", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key <> ''"),
    )


def downgrade() -> None:
    op.drop_index("uq_tool_invocations_owner_idempotency", table_name="tool_invocations")
