"""Unit tests for RobotStatusService.compute."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leitstand_backend.application.robot_status_service import RobotStatusService
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_robot_state_view import InMemoryRobotStateView
from tests.fakes.runs import mission_run

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _svc(robots, runs, state_view) -> RobotStatusService:
    return RobotStatusService(robots, runs, state_view)


async def _assign(runs: InMemoryMissionRunRepository, robot_id: str, status: RunStatus):
    await runs.create(mission_run(robot_id=robot_id, status=status, when=_NOW))


@pytest.mark.asyncio
async def test_compute_unknown_robot_returns_none() -> None:
    svc = _svc(InMemoryRobotRepository(), InMemoryMissionRunRepository(), InMemoryRobotStateView())
    assert await svc.compute("ghost") is None


@pytest.mark.asyncio
async def test_compute_idle_when_online_without_executing_mission() -> None:
    robots = InMemoryRobotRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    assert (
        await _svc(robots, InMemoryMissionRunRepository(), InMemoryRobotStateView()).compute("r1")
        is RobotStatus.IDLE
    )


@pytest.mark.asyncio
async def test_compute_active_when_mission_executing() -> None:
    robots = InMemoryRobotRepository()
    runs = InMemoryMissionRunRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await _assign(runs, "r1", RunStatus.RUNNING)
    assert await _svc(robots, runs, InMemoryRobotStateView()).compute("r1") is RobotStatus.ACTIVE


@pytest.mark.asyncio
async def test_compute_idle_when_mission_only_assigned() -> None:
    robots = InMemoryRobotRepository()
    runs = InMemoryMissionRunRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await _assign(runs, "r1", RunStatus.PENDING)  # pending is not executing
    assert await _svc(robots, runs, InMemoryRobotStateView()).compute("r1") is RobotStatus.IDLE


@pytest.mark.asyncio
async def test_compute_offline_overrides_executing_mission() -> None:
    robots = InMemoryRobotRepository()
    runs = InMemoryMissionRunRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await robots.record_offline("r1")
    await _assign(runs, "r1", RunStatus.RUNNING)
    assert await _svc(robots, runs, InMemoryRobotStateView()).compute("r1") is RobotStatus.OFFLINE


@pytest.mark.asyncio
async def test_compute_charging_when_battery_charging() -> None:
    robots = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    await robots.record_online("r1", Metadata(id="r1"))
    state_view.set_battery("r1", Battery(ts=_NOW, battery_pct=50, charging=True))
    assert (
        await _svc(robots, InMemoryMissionRunRepository(), state_view).compute("r1")
        is RobotStatus.CHARGING
    )
