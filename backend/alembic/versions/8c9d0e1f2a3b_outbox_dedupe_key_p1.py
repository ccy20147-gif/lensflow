"""Add dedupe_key + partial unique index to outbox_events (TF-WF-006 P1).

Revision ID: 8c9d0e1f2a3b
Revises: 7a8b9c0d1e2f

The Foundation contract requires every ``provider_dispatch`` outbox row
to carry a ``dedupe_key`` pinned to the ``provider_attempt_id`` (and
similarly for ``result_publish``).  This migration:

  1. Adds the nullable ``dedupe_key`` column.
  2. Backfills the column for every existing ``provider_dispatch`` and
     ``result_publish`` row whose ``aggregate_id`` is a UUID — the same
     value the application would assign on insert.
  3. Creates a partial unique index that enforces
     ``(purpose, dedupe_key)`` uniqueness only for the two Foundation
     purposes.  ``notification`` and ``provider_reconcile`` purposes
     stay non-unique so their existing rows (and their re-delivery
     semantics) remain untouched.

Downgrade drops the index then the column; the Foundation scope does
not promise backwards compatibility on rollback.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c9d0e1f2a3b"
down_revision: Union[str, Sequence[str], None] = "7a8b9c0d1e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
    )
    # Backfill existing rows so the unique index can be enforced.
    # provider_dispatch + result_publish dedupe on aggregate_id (the
    # canonical ProviderInvocationAttempt id under Foundation scope).
    op.execute(
        "UPDATE outbox_events SET dedupe_key = aggregate_id::text "
        "WHERE purpose IN ('provider_dispatch', 'result_publish') "
        "AND dedupe_key IS NULL"
    )
    op.create_index(
        "ix_outbox_events_dedupe_key",
        "outbox_events",
        ["dedupe_key"],
        unique=False,
    )
    op.create_index(
        "uq_outbox_events_purpose_dedupe_key",
        "outbox_events",
        ["purpose", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "purpose IN ('provider_dispatch', 'result_publish') "
            "AND dedupe_key IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_outbox_events_purpose_dedupe_key",
        table_name="outbox_events",
    )
    op.drop_index(
        "ix_outbox_events_dedupe_key",
        table_name="outbox_events",
    )
    op.drop_column("outbox_events", "dedupe_key")