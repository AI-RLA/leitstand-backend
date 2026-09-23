"""The Postgres mission and run repositories against real rows, which the in-memory fakes imitate.

Covers what only the database enforces: the site a mission's history pins, the stage rows and
their nesting, one active run per robot, header ordering, cascades and the transition log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.mission_repository_adapter import (
    PostgresMissionRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.mission_run_repository_adapter import (
    PostgresMissionRunRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.site_repository_adapter import (
    PostgresSiteRepositoryAdapter,
)
from leitstand_backend.domain.errors import RobotBusy
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_run import MissionRun
from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from tests.fakes.runs import mission_run

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


async def _site(session: AsyncSession) -> UUID:
    site = await PostgresSiteRepositoryAdapter(session).create(
        name=f"site-{uuid4()}",
        anchor_lat=52.0,
        anchor_lon=8.0,
        anchor_heading_deg=0.0,
        nav2_map_ref="map",
        outline=None,
        description=None,
    )
    return site.site_id


def _mission(site_id: UUID | None) -> Mission:
    waypoint = (
        SiteLocalWaypoint(site_id=site_id, x=1.0, y=1.0)
        if site_id
        else WGS84Waypoint(lat=52.0, lon=8.0)
    )
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[
            NavigationStage(
                stage_id=uuid4(),
                waypoints=[waypoint],
                on_cancel=[NavigationStage(stage_id=uuid4(), waypoints=[waypoint])],
            )
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(mission: Mission, robot_id: str, status: RunStatus) -> MissionRun:
    return mission_run(
        robot_id=robot_id,
        status=status,
        mission_id=mission.mission_id,
        stages=mission.stages,
        when=_NOW,
    )


def _record(stage_id: UUID, status: StageStatus, header_id: int) -> StageStateRecord:
    return StageStateRecord(
        stage_id=stage_id, header_id=header_id, stage_index=0, status=status, source_ts=_NOW
    )


async def test_a_site_used_by_history_stays_and_the_guard_names_the_mission(
    db_session: AsyncSession,
) -> None:
    missions = PostgresMissionRepositoryAdapter(db_session)
    runs = PostgresMissionRunRepositoryAdapter(db_session)
    sites = PostgresSiteRepositoryAdapter(db_session)
    site_id = await _site(db_session)
    mission = await missions.save(_mission(site_id))
    await runs.create(_run(mission, f"robot-{uuid4()}", RunStatus.SUCCEEDED))

    # The blocking rule lives in the service, because it spans two aggregates.
    assert await missions.missions_referencing_site(site_id) == [mission.mission_id]

    await missions.archive(mission.mission_id)
    assert await missions.missions_referencing_site(site_id) == [mission.mission_id]

    # A never-run mission can be deleted outright, and that releases the site.
    other_site = await _site(db_session)
    draft = await missions.save(_mission(other_site))
    await missions.delete(draft.mission_id)
    assert await missions.missions_referencing_site(other_site) == []
    assert await sites.delete(other_site) is True


async def test_stages_round_trip_through_rows_with_cleanup_nested(db_session: AsyncSession) -> None:
    missions = PostgresMissionRepositoryAdapter(db_session)
    site_id = await _site(db_session)
    mission = _mission(site_id)
    await missions.save(mission)

    loaded = await missions.get(mission.mission_id)

    assert loaded is not None and loaded.stages == mission.stages
    assert await missions.missions_referencing_site(site_id) == [mission.mission_id]


async def test_one_robot_holds_one_active_run_in_the_database(db_session: AsyncSession) -> None:
    missions = PostgresMissionRepositoryAdapter(db_session)
    runs = PostgresMissionRunRepositoryAdapter(db_session)
    first, second = await missions.save(_mission(None)), await missions.save(_mission(None))
    robot = f"robot-{uuid4()}"
    held = await runs.create(_run(first, robot, RunStatus.RUNNING))

    with pytest.raises(RobotBusy) as excinfo:
        await runs.create(_run(second, robot, RunStatus.PENDING))
    assert excinfo.value.active_mission_id == first.mission_id

    # Two runs of one mission on two robots are fine, and the CAS never leaves terminal.
    await runs.create(_run(first, f"robot-{uuid4()}", RunStatus.RUNNING))
    assert len(await runs.list_active_by_mission(first.mission_id)) == 2
    assert await runs.update_status(
        held.run_id, RunStatus.SUCCEEDED, expected=RunStatus.RUNNING, trigger=RunTrigger.COMPLETE
    )
    assert (
        await runs.update_status(
            held.run_id, RunStatus.FAILED, expected=RunStatus.SUCCEEDED, trigger=RunTrigger.FAIL
        )
        is None
    )


async def test_stage_rows_keep_the_freshest_frame_per_run(db_session: AsyncSession) -> None:
    missions = PostgresMissionRepositoryAdapter(db_session)
    runs = PostgresMissionRunRepositoryAdapter(db_session)
    mission = await missions.save(_mission(None))
    run = await runs.create(_run(mission, f"robot-{uuid4()}", RunStatus.RUNNING))
    sid = mission.stages[0].stage_id

    await runs.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.RUNNING, 2)])
    await runs.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.WAITING, 1)])
    (row,) = await runs.get_stage_runs(run.run_id)
    assert row.status is StageStatus.RUNNING
    latest = await runs.latest_by_mission([mission.mission_id])
    assert latest[mission.mission_id].run_id == run.run_id


async def test_deleting_a_run_removes_its_stage_rows_in_the_database(
    db_session: AsyncSession,
) -> None:
    missions = PostgresMissionRepositoryAdapter(db_session)
    runs = PostgresMissionRunRepositoryAdapter(db_session)
    mission = await missions.save(_mission(None))
    run = await runs.create(_run(mission, f"robot-{uuid4()}", RunStatus.FAILED))
    await runs.overwrite_stage_runs(
        run.run_id, [_record(mission.stages[0].stage_id, StageStatus.FAILED, 1)]
    )
    await runs.delete(run.run_id)
    assert await runs.get(run.run_id) is None
    assert await runs.get_stage_runs(run.run_id) == []


async def test_the_transition_log_records_requests_and_receipts(db_session: AsyncSession) -> None:
    """The row is appended with the status write, and the receipt merges into it afterwards."""
    runs = PostgresMissionRunRepositoryAdapter(db_session)
    missions = PostgresMissionRepositoryAdapter(db_session)
    mission = await missions.save(_mission(None))
    run = await runs.create(_run(mission, f"robot-{uuid4()}", RunStatus.RUNNING))

    await runs.update_status(
        run.run_id,
        RunStatus.CANCELLING,
        expected=RunStatus.RUNNING,
        trigger=RunTrigger.CANCEL_REQUEST,
        report_header_id=7,
        detail={"mode": "graceful"},
    )
    await runs.set_acknowledged(run.run_id, False, {"reason": "estop latched"})

    rows = await runs.list_transitions(run.run_id)
    assert [(r.from_status, r.to_status, r.trigger, r.actor) for r in rows] == [
        (RunStatus.RUNNING, RunStatus.CANCELLING, RunTrigger.CANCEL_REQUEST, "operator")
    ]
    assert rows[0].report_header_id == 7
    assert rows[0].acknowledged is False
    assert rows[0].detail == {"mode": "graceful", "reason": "estop latched"}
    assert (await runs.get_for_update(run.run_id)).transitions == rows
