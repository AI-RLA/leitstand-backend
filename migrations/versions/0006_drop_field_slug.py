"""drop slug column from fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-07

"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres drops the implicit unique index on slug automatically.
    op.execute("ALTER TABLE fields DROP COLUMN slug;")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade not supported: slug values are unrecoverable without a backfill. "
        "Restore from a DB backup instead."
    )
