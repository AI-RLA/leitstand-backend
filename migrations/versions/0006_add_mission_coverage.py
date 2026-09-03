"""record how a generated mission was planned, and store field area finely enough to check it

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# Three decimals of a hectare quantises area to 10 m2, which is invisible on a field and a large
# fraction of a test plot: a 174 m2 field stored as 170, so a plan could cover more ground than the
# field was recorded as having. Six decimals resolve a hundredth of a square metre.
_AREA_COLUMN = """
ALTER TABLE fields ADD COLUMN area_ha NUMERIC({precision})
  GENERATED ALWAYS AS (ST_Area(geometry::geography) / 10000.0) STORED;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE missions ADD COLUMN coverage JSONB;")
    # Dropped and re-added rather than altered: the column is generated, so it carries no data of
    # its own and every row is recomputed from the geometry on the way back in.
    op.execute("ALTER TABLE fields DROP COLUMN IF EXISTS area_ha;")
    op.execute(_AREA_COLUMN.format(precision="12, 6"))


def downgrade() -> None:
    op.execute("ALTER TABLE fields DROP COLUMN IF EXISTS area_ha;")
    op.execute(_AREA_COLUMN.format(precision="8, 3"))
    op.execute("ALTER TABLE missions DROP COLUMN IF EXISTS coverage;")
