"""create fields table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE fields (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name        text NOT NULL,
          slug        text UNIQUE NOT NULL,
          geometry    geometry(Polygon, 4326) NOT NULL CHECK (ST_IsValid(geometry)),
          area_ha     numeric(8, 3) GENERATED ALWAYS AS (ST_Area(geometry::geography) / 10000.0) STORED,
          notes       text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("CREATE INDEX fields_geometry_gist ON fields USING GIST (geometry);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fields;")
