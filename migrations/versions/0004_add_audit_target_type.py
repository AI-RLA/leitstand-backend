"""add target_type to audit_log

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-03

"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN target_type text;")
    op.execute("CREATE INDEX audit_log_target_type_idx ON audit_log (target_type);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS audit_log_target_type_idx;")
    op.execute("ALTER TABLE audit_log DROP COLUMN target_type;")
