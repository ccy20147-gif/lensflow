"""Persist full-draft compare-and-swap hashes for WorkflowDraft.

TF-WF-004 requires the durable save path to use a CAS token that
covers the entire draft (graph + config + layout + draft_version).
Two pure layout saves would otherwise pass a graph-hash-only CAS and
the second tab would silently overwrite the first.  This migration
adds the ``workflow_drafts.full_draft_hash`` column populated from
``compute_full_draft_hash`` and a forward-compatible
``outbox_events`` index for activation evidence.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f0a1b2c3d4e5"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_drafts",
        sa.Column("full_draft_hash", sa.String(64), nullable=False, server_default=""),
    )
    # Backfill any legacy rows so reads return a stable token until the
    # next save regenerates the canonical value.  ``sha256`` is built
    # into PostgreSQL; ``digest(text, text)`` requires the pgcrypto
    # extension, which is not always present in shared dev databases.
    op.execute(
        """
        UPDATE workflow_drafts
           SET full_draft_hash = encode(
                 sha256(
                   ('graph:' || coalesce(graph_hash, '') || '|' ||
                    'layout:' || coalesce(layout_hash, '') || '|' ||
                    'exec:'  || coalesce(execution_hash, '') || '|' ||
                    'v:'     || coalesce(draft_version::text, '0'))::bytea
                 ),
                 'hex'
               )
        """
    )
    # Activation outbox events are queried by aggregate; this index
    # keeps the ``workflow.revision.activated`` projection cheap.
    op.create_index(
        "ix_outbox_events_workflow_revision_activated",
        "outbox_events",
        ["aggregate_id", "event_type"],
        postgresql_where=sa.text("event_type = 'workflow.revision.activated'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_events_workflow_revision_activated",
        table_name="outbox_events",
    )
    op.drop_column("workflow_drafts", "full_draft_hash")
