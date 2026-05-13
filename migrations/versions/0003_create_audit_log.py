"""create audit_log table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_log (
          id          bigserial PRIMARY KEY,
          ts          timestamptz NOT NULL DEFAULT now(),
          user_id     text,
          action      text NOT NULL,
          target_id   text,
          payload     jsonb
        );
    """)
    op.execute("CREATE INDEX audit_log_ts_idx ON audit_log (ts DESC);")
    op.execute("CREATE INDEX audit_log_user_idx ON audit_log (user_id);")
    op.execute("CREATE INDEX audit_log_target_idx ON audit_log (target_id);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log;")
