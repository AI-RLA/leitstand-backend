"""add missions, sites, mission_site_refs, and mission_stage_state tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE missions (
          mission_id     uuid        NOT NULL,
          update_id      integer     NOT NULL DEFAULT 0,
          name           text        NOT NULL,
          description    text,
          status         text        NOT NULL DEFAULT 'DRAFT',
          stages         jsonb       NOT NULL,
          robot_id       text,
          dispatched_at  timestamptz,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now(),
          failure_errors jsonb,
          PRIMARY KEY (mission_id, update_id)
        );
    """)
    op.execute("CREATE INDEX ix_missions_robot_id_status ON missions (robot_id, status);")
    op.execute("CREATE INDEX ix_missions_status_dispatched ON missions (status, dispatched_at);")
    op.execute("CREATE INDEX ix_missions_stages_gin ON missions USING gin (stages);")

    op.execute("""
        CREATE TABLE sites (
          site_id            uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
          name               text             NOT NULL UNIQUE,
          anchor_lat         double precision NOT NULL,
          anchor_lon         double precision NOT NULL,
          anchor_heading_deg double precision NOT NULL,
          nav2_map_ref       text             NOT NULL,
          outline            jsonb,
          description        text,
          created_at         timestamptz      NOT NULL DEFAULT now(),
          updated_at         timestamptz      NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE mission_site_refs (
          mission_id uuid NOT NULL,
          site_id    uuid NOT NULL REFERENCES sites (site_id) ON DELETE RESTRICT,
          PRIMARY KEY (mission_id, site_id)
        );
    """)
    op.execute("CREATE INDEX ix_mission_site_refs_site_id ON mission_site_refs (site_id);")

    op.execute("""
        CREATE TABLE mission_stage_state (
          mission_id  uuid             NOT NULL,
          stage_id    uuid             NOT NULL,
          header_id   bigint           NOT NULL DEFAULT 0,
          stage_index integer          NOT NULL,
          status      text             NOT NULL,
          progress    double precision NOT NULL DEFAULT 0,
          started_at  timestamptz,
          ended_at    timestamptz,
          result      jsonb,
          updated_at  timestamptz      NOT NULL,
          source_ts   timestamptz      NOT NULL,
          PRIMARY KEY (mission_id, stage_id)
        );
    """)
    op.execute("CREATE INDEX ix_mission_stage_state_status ON mission_stage_state (status);")
    op.execute(
        "CREATE INDEX ix_mission_stage_state_mission_index "
        "ON mission_stage_state (mission_id, stage_index);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mission_stage_state;")
    op.execute("DROP TABLE IF EXISTS mission_site_refs;")
    op.execute("DROP TABLE IF EXISTS sites;")
    op.execute("DROP TABLE IF EXISTS missions;")
