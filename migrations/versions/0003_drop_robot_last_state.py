"""drop robots.last_state (robot status is backend-derived, not robot-reported)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE robots DROP COLUMN IF EXISTS last_state;")


def downgrade() -> None:
    op.execute("ALTER TABLE robots ADD COLUMN last_state jsonb;")
