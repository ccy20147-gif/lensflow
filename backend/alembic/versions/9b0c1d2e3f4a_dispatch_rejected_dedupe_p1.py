"""Add partial unique index for provider_dispatch_rejected outbox dedupe (P1).

Revision ID: 9b0c1d2e3f4a
Revises: 8c9d0e1f2a3b

The previous Foundation migration ``8c9d0e1f2a3b_outbox_dedupe_key_p1``
added a partial unique index covering ``provider_dispatch`` and
``result_publish`` purposes only.  The V0 hardening round extended the
discard audit row to use ``purpose='provider_dispatch_rejected'``; that
purpose is not covered by the existing partial index, so duplicate late
callbacks would each insert their own audit row.

This migration adds a second partial unique index that covers the new
purpose.  Combined with the existing two-purpose index, every Foundation
+ V0 outbox dedupe invariant is enforced at the database boundary:

  * ``provider_dispatch``        — exactly one outbox per provider_attempt_id
  * ``result_publish``           — exactly one outbox per provider_attempt_id
  * ``provider_dispatch_rejected`` — at most one discarded audit row per
    provider_attempt_id

Existing rows are not backfilled; no rows exist for the new purpose yet
because the service-side change ships in the same commit as this
migration.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b0c1d2e3f4a"
down_revision: Union[str, Sequence[str], None] = "8c9d0e1f2a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_outbox_events_dispatch_rejected",
        "outbox_events",
        ["purpose", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "purpose = 'provider_dispatch_rejected' AND dedupe_key IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_outbox_events_dispatch_rejected",
        table_name="outbox_events",
    )
