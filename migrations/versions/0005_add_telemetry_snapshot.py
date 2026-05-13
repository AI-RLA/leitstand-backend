"""add last_pose / last_battery / last_state snapshot columns to robots

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE robots ADD COLUMN last_pose jsonb;")
    op.execute("ALTER TABLE robots ADD COLUMN last_battery jsonb;")
    op.execute("ALTER TABLE robots ADD COLUMN last_state jsonb;")


def downgrade() -> None:
    op.execute("ALTER TABLE robots DROP COLUMN last_pose;")
    op.execute("ALTER TABLE robots DROP COLUMN last_battery;")
    op.execute("ALTER TABLE robots DROP COLUMN last_state;")
