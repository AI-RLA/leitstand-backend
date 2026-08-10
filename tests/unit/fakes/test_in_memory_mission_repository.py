"""Contract tests for the in-memory MissionRepository's stage-state behaviour."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.domain.model.mission.mission import (
    Mission,
    MissionStatus,
    NavigationStage,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository

_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)])],
        created_at=_T0,
        updated_at=_T0,
    )


def _record(
    stage_id: UUID, status: StageStatus, header_id: int, source_ts: datetime = _T0
) -> StageStateRecord:
    return StageStateRecord(
        stage_id=stage_id, header_id=header_id, stage_index=0, status=status, source_ts=source_ts
    )


@pytest.mark.asyncio
async def test_upsert_keeps_the_freshest_frame() -> None:
    repo = InMemoryMissionRepository()
    mission = _mission()
    await repo.save(mission)
    sid = mission.stages[0].stage_id

    await repo.upsert_stage_states(mission.mission_id, [_record(sid, StageStatus.RUNNING, 2)])
    # A stale frame (lower header_id) must not regress the stage.
    await repo.upsert_stage_states(mission.mission_id, [_record(sid, StageStatus.WAITING, 1)])
    assert (await repo.get_stage_states(mission.mission_id))[0].status is StageStatus.RUNNING

    # A fresher frame (higher header_id) overwrites it.
    await repo.upsert_stage_states(mission.mission_id, [_record(sid, StageStatus.FINISHED, 3)])
    assert (await repo.get_stage_states(mission.mission_id))[0].status is StageStatus.FINISHED


@pytest.mark.asyncio
async def test_overwrite_ignores_header_ordering() -> None:
    repo = InMemoryMissionRepository()
    mission = _mission()
    await repo.save(mission)
    sid = mission.stages[0].stage_id

    await repo.upsert_stage_states(mission.mission_id, [_record(sid, StageStatus.RUNNING, 5)])
    # The terminal resolve is authoritative: it overwrites even at an equal header_id, where
    # the versioned upsert would have kept the old row.
    await repo.overwrite_stage_states(mission.mission_id, [_record(sid, StageStatus.CANCELLED, 5)])
    assert (await repo.get_stage_states(mission.mission_id))[0].status is StageStatus.CANCELLED


@pytest.mark.asyncio
async def test_reset_clears_stage_state_and_failure_errors() -> None:
    repo = InMemoryMissionRepository()
    mission = _mission()
    await repo.save(mission)
    sid = mission.stages[0].stage_id
    error = MissionError(
        origin=ErrorOrigin.ROBOT, severity=ErrorSeverity.FATAL, type="x", description="y"
    )
    await repo.update_status(mission.mission_id, MissionStatus.FAILED, errors=[error])
    await repo.overwrite_stage_states(mission.mission_id, [_record(sid, StageStatus.FAILED, 1)])

    await repo.reset_to_draft(mission.mission_id)

    assert await repo.get_stage_states(mission.mission_id) == []
    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is None
