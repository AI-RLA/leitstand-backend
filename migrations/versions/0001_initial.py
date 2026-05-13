"""initial: postgis + pgcrypto extensions, robots, fields, audit_log tables

Revision ID: 0001
Revises:
Create Date: 2026-05-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "robots",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "online",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_pose", postgresql.JSONB(), nullable=True),
        sa.Column("last_battery", postgresql.JSONB(), nullable=True),
        sa.Column("last_state", postgresql.JSONB(), nullable=True),
    )

    op.execute("""
        CREATE TABLE fields (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name        text NOT NULL,
          geometry    geometry(Polygon, 4326) NOT NULL CHECK (ST_IsValid(geometry)),
          area_ha     numeric(8, 3) GENERATED ALWAYS AS (ST_Area(geometry::geography) / 10000.0) STORED,
          notes       text,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
    """)
    op.execute("CREATE INDEX fields_geometry_gist ON fields USING GIST (geometry);")

    op.execute("""
        CREATE TABLE audit_log (
          id          bigserial PRIMARY KEY,
          ts          timestamptz NOT NULL DEFAULT now(),
          user_id     text,
          action      text NOT NULL,
          target_id   text,
          payload     jsonb,
          target_type text
        );
    """)
    op.execute("CREATE INDEX audit_log_ts_idx ON audit_log (ts DESC);")
    op.execute("CREATE INDEX audit_log_user_idx ON audit_log (user_id);")
    op.execute("CREATE INDEX audit_log_target_idx ON audit_log (target_id);")
    op.execute("CREATE INDEX audit_log_target_type_idx ON audit_log (target_type);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log;")
    op.execute("DROP TABLE IF EXISTS fields;")
    op.drop_table("robots")
