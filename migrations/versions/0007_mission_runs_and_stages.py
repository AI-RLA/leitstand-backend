"""missions become definitions; runs own execution; stages become rows

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Execution state moves to the run tables below and coverage provenance into its stage; no
    # rows are carried over, because there is no production data.
    op.execute("DROP TABLE mission_stage_state;")
    op.execute("DROP TABLE mission_site_refs;")

    # Dropping update_id would take the composite primary key with it, so the key goes first and
    # comes back single-column once the execution columns are gone.
    op.execute("ALTER TABLE missions DROP CONSTRAINT missions_pkey;")
    op.execute("""
        ALTER TABLE missions
          DROP COLUMN status,
          DROP COLUMN dispatched_at,
          DROP COLUMN failure_errors,
          DROP COLUMN stages,
          DROP COLUMN update_id,
          DROP COLUMN coverage,
          ADD COLUMN archived_at timestamptz;
    """)
    op.execute("ALTER TABLE missions RENAME COLUMN robot_id TO assigned_robot_id;")
    op.execute("ALTER TABLE missions ADD PRIMARY KEY (mission_id);")
    op.execute("DROP INDEX IF EXISTS ix_missions_stages_gin;")
    op.execute("DROP INDEX IF EXISTS ix_missions_robot_id_status;")
    op.execute("DROP INDEX IF EXISTS ix_missions_status_dispatched;")

    op.execute("""
        CREATE TABLE mission_stages (
          stage_id        uuid    PRIMARY KEY,
          mission_id      uuid    NOT NULL REFERENCES missions (mission_id) ON DELETE CASCADE,
          sequence        integer NOT NULL,
          kind            text    NOT NULL,
          site_id         uuid    REFERENCES sites (site_id) ON DELETE RESTRICT,
          parent_stage_id uuid    REFERENCES mission_stages (stage_id) ON DELETE CASCADE,
          spec            jsonb   NOT NULL,
          -- NULLS NOT DISTINCT: every top-level stage has a NULL parent, and a plain UNIQUE would
          -- let all of them share a sequence. DEFERRABLE so a reorder can be written in one
          -- statement without stepping on itself.
          CONSTRAINT uq_stage_order UNIQUE NULLS NOT DISTINCT (mission_id, parent_stage_id, sequence)
            DEFERRABLE INITIALLY DEFERRED
        );
    """)
    op.execute("CREATE INDEX ix_mission_stages_mission ON mission_stages (mission_id, sequence);")
    op.execute("CREATE INDEX ix_mission_stages_site ON mission_stages (site_id);")

    op.execute("""
        CREATE TABLE mission_runs (
          run_id         uuid        PRIMARY KEY,
          mission_id     uuid        NOT NULL REFERENCES missions (mission_id) ON DELETE RESTRICT,
          robot_id       text        NOT NULL,
          status         text        NOT NULL,
          stages         jsonb       NOT NULL,
          stages_digest  text        NOT NULL,
          site_anchors   jsonb,
          origin         jsonb       NOT NULL,
          notes          text,
          failure_errors jsonb,
          created_at     timestamptz NOT NULL DEFAULT now(),
          dispatched_at  timestamptz,
          started_at     timestamptz,
          ended_at       timestamptz,
          last_frame_at  timestamptz,
          updated_at     timestamptz NOT NULL DEFAULT now()
        );
    """)
    # One robot, one run, enforced here so two dispatchers racing for the same robot cannot both
    # win; one mission, one run is a service decision under the mission's row lock, not an index.
    op.execute("""
        CREATE UNIQUE INDEX uq_run_robot_active ON mission_runs (robot_id)
          WHERE status IN ('PENDING', 'DISPATCHED', 'RUNNING', 'PAUSED');
    """)
    op.execute("""
        CREATE INDEX ix_run_mission_active ON mission_runs (mission_id)
          WHERE status IN ('PENDING', 'DISPATCHED', 'RUNNING', 'PAUSED');
    """)
    op.execute("CREATE INDEX ix_run_mission_created ON mission_runs (mission_id, created_at DESC);")

    # Two clocks: the robot's (reported_*, occurred_at) is only ever copied from a frame; the
    # backend's (recorded_at) is authoritative for ordering questions across runs. status_source
    # records whether the robot reported the status or the backend projected it at run end.
    op.execute("""
        CREATE TABLE stage_runs (
          run_id              uuid             NOT NULL REFERENCES mission_runs (run_id) ON DELETE RESTRICT,
          stage_id            uuid             NOT NULL,
          header_id           bigint           NOT NULL DEFAULT 0,
          stage_index         integer          NOT NULL,
          status              text             NOT NULL,
          progress            double precision NOT NULL DEFAULT 0,
          result              jsonb,
          reported_started_at timestamptz,
          reported_ended_at   timestamptz,
          occurred_at         timestamptz      NOT NULL,
          recorded_at         timestamptz      NOT NULL,
          status_source       text             NOT NULL,
          PRIMARY KEY (run_id, stage_id)
        );
    """)
    op.execute("CREATE INDEX ix_stage_runs_index ON stage_runs (run_id, stage_index);")


def downgrade() -> None:
    raise NotImplementedError("lossy: the pre-run schema cannot be rebuilt from this one")
