"""transition log, last report, cleanup stage rows, transitional run states

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_OLD_ACTIVE = "('PENDING', 'DISPATCHED', 'RUNNING', 'PAUSED')"
_NEW_ACTIVE = "('PENDING', 'DISPATCHED', 'RUNNING', 'PAUSED', 'PAUSING', 'RESUMING', 'CANCELLING')"


def _recreate_active_indexes(active: str) -> None:
    op.execute("DROP INDEX IF EXISTS uq_run_robot_active;")
    op.execute("DROP INDEX IF EXISTS ix_run_mission_active;")
    op.execute(
        f"CREATE UNIQUE INDEX uq_run_robot_active ON mission_runs (robot_id) "
        f"WHERE status IN {active};"
    )
    op.execute(
        f"CREATE INDEX ix_run_mission_active ON mission_runs (mission_id) WHERE status IN {active};"
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE mission_run_transitions (
            id bigserial PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES mission_runs(run_id) ON DELETE CASCADE,
            from_status text NOT NULL,
            to_status text NOT NULL,
            trigger text NOT NULL,
            at timestamptz NOT NULL,
            actor text NOT NULL,
            report_header_id bigint,
            acknowledged boolean,
            detail jsonb
        );
    """)
    op.execute("CREATE INDEX ix_run_transitions_run ON mission_run_transitions (run_id, id);")
    op.execute("ALTER TABLE mission_runs ADD COLUMN last_report jsonb;")
    op.execute("ALTER TABLE mission_runs DROP COLUMN last_frame_at;")
    op.execute("ALTER TABLE stage_runs ADD COLUMN parent_stage_id uuid;")
    # The partial indexes must know the three new active states, or a run in one of them would
    # neither block a second run on the robot nor count as active.
    _recreate_active_indexes(_NEW_ACTIVE)


def downgrade() -> None:
    _recreate_active_indexes(_OLD_ACTIVE)
    op.execute("ALTER TABLE stage_runs DROP COLUMN parent_stage_id;")
    op.execute("ALTER TABLE mission_runs ADD COLUMN last_frame_at timestamptz;")
    op.execute(
        "UPDATE mission_runs SET last_frame_at = (last_report->>'received_at')::timestamptz;"
    )
    op.execute("ALTER TABLE mission_runs DROP COLUMN last_report;")
    op.execute("DROP TABLE mission_run_transitions;")
