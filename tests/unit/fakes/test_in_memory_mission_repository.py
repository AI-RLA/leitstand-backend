"""Contract tests for the in-memory run repository's invariants."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.domain.errors import RobotBusy
from leitstand_backend.domain.model.mission.mission_run import MissionRun
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.runs import mission_run

_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _run(robot_id: str = "r1", status: RunStatus = RunStatus.PENDING) -> MissionRun:
    return mission_run(robot_id=robot_id, status=status, when=_T0)


def _record(
    stage_id: UUID, status: StageStatus, header_id: int, source_ts: datetime = _T0
) -> StageStateRecord:
    return StageStateRecord(
        stage_id=stage_id, header_id=header_id, stage_index=0, status=status, source_ts=source_ts
    )


@pytest.mark.asyncio
async def test_upsert_keeps_the_freshest_frame() -> None:
    repo = InMemoryMissionRunRepository()
    run = await repo.create(_run())
    sid = run.stages[0].stage_id

    await repo.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.RUNNING, 2)])
    # A stale frame (lower header_id) must not regress the stage.
    await repo.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.WAITING, 1)])
    assert (await repo.get_stage_runs(run.run_id))[0].status is StageStatus.RUNNING

    await repo.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.FINISHED, 3)])
    assert (await repo.get_stage_runs(run.run_id))[0].status is StageStatus.FINISHED


@pytest.mark.asyncio
async def test_overwrite_ignores_header_ordering() -> None:
    repo = InMemoryMissionRunRepository()
    run = await repo.create(_run())
    sid = run.stages[0].stage_id

    await repo.upsert_stage_runs(run.run_id, [_record(sid, StageStatus.RUNNING, 5)])
    await repo.overwrite_stage_runs(run.run_id, [_record(sid, StageStatus.CANCELLED, 5)])
    assert (await repo.get_stage_runs(run.run_id))[0].status is StageStatus.CANCELLED


@pytest.mark.asyncio
async def test_status_is_compare_and_set_and_never_leaves_terminal() -> None:
    repo = InMemoryMissionRunRepository()
    run = await repo.create(_run())
    assert (
        await repo.update_status(run.run_id, RunStatus.RUNNING, expected=RunStatus.PENDING)
    ) is not None
    assert (await repo.get(run.run_id)).started_at is not None
    assert (
        await repo.update_status(run.run_id, RunStatus.SUCCEEDED, expected=RunStatus.RUNNING)
    ) is not None
    assert (await repo.get(run.run_id)).ended_at is not None
    assert (
        await repo.update_status(run.run_id, RunStatus.FAILED, expected=RunStatus.SUCCEEDED)
    ) is None
    assert (
        await repo.update_status(run.run_id, RunStatus.FAILED, expected=RunStatus.RUNNING)
    ) is None
    assert (await repo.get(run.run_id)).status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_one_robot_holds_one_active_run() -> None:
    repo = InMemoryMissionRunRepository()
    first = await repo.create(_run(robot_id="r1"))
    with pytest.raises(RobotBusy) as excinfo:
        await repo.create(_run(robot_id="r1"))
    assert excinfo.value.active_mission_id == first.mission_id
    # A finished run releases the robot.
    await repo.update_status(first.run_id, RunStatus.SUCCEEDED, expected=RunStatus.PENDING)
    await repo.create(_run(robot_id="r1"))


@pytest.mark.asyncio
async def test_two_runs_of_one_mission_on_two_robots_coexist() -> None:
    repo = InMemoryMissionRunRepository()
    a = await repo.create(_run(robot_id="r1"))
    b = a.model_copy(update={"run_id": uuid4(), "robot_id": "r2"})
    await repo.create(b)
    assert {r.run_id for r in await repo.list_active_by_mission(a.mission_id)} == {
        a.run_id,
        b.run_id,
    }


@pytest.mark.asyncio
async def test_latest_by_mission_picks_the_newest() -> None:
    repo = InMemoryMissionRunRepository()
    a = await repo.create(_run(status=RunStatus.SUCCEEDED))
    later = a.model_copy(
        update={"run_id": uuid4(), "created_at": _T0.replace(hour=13), "robot_id": "r2"}
    )
    await repo.create(later)
    latest = await repo.latest_by_mission([a.mission_id])
    assert latest[a.mission_id].run_id == later.run_id
