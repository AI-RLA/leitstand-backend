"""add tool_calls and audit provenance

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-16
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tool_calls (
          id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id               text        NOT NULL,
          provider_tool_call_id text        NOT NULL,
          tool_name             text        NOT NULL,
          arguments             jsonb,
          result                jsonb,
          status                text        NOT NULL,
          decided_by            text,
          decided_at            timestamptz,
          error                 text,
          -- Stamped when the proposal is recorded, because an outcome landing later copies its
          -- provenance from this row rather than from the turn, which is over by then.
          model                 text,
          prompt_version        text,
          created_at            timestamptz NOT NULL DEFAULT now()
        );
    """)
    # The approval lookup's whole predicate: an operator's rows carrying one provider-minted id.
    op.execute(
        "CREATE INDEX ix_tool_calls_owner_call ON tool_calls (user_id, provider_tool_call_id);"
    )

    # actor/authority default to human/direct: every row predating the first agent write tool is
    # provably a human acting directly, so the backfill is correct rather than a guess. The links
    # back to the agent turn are denormalised, since chat data faces a shorter retention.
    op.execute(
        """
        ALTER TABLE audit_log
            ADD COLUMN actor TEXT NOT NULL DEFAULT 'human',
            ADD COLUMN authority TEXT NOT NULL DEFAULT 'direct',
            ADD COLUMN decided_by TEXT,
            ADD COLUMN model TEXT,
            ADD COLUMN prompt_version TEXT,
            ADD COLUMN tool_call_id UUID;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE audit_log
            DROP COLUMN actor,
            DROP COLUMN authority,
            DROP COLUMN decided_by,
            DROP COLUMN model,
            DROP COLUMN prompt_version,
            DROP COLUMN tool_call_id;
        """
    )
    op.execute("DROP TABLE IF EXISTS tool_calls;")
