"""persist the robot factsheet

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE robots ADD COLUMN factsheet_json JSONB;")


def downgrade() -> None:
    op.execute("ALTER TABLE robots DROP COLUMN IF EXISTS factsheet_json;")
