"""Unit tests for RobotStatusService.compute."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from leitstand_backend.application.robot_status_service import RobotStatusService
from leitstand_backend.domain.model.mission.mission import Mission, MissionStatus, NavigationStage
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_robot_state_view import InMemoryRobotStateView

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _svc(robots, missions, state_view) -> RobotStatusService:
    return RobotStatusService(robots, missions, state_view)


def _mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _assign(missions: InMemoryMissionRepository, robot_id: str, status: MissionStatus):
    mission = _mission()
    await missions.save(mission)
    await missions.assign_robot(mission.mission_id, robot_id, _NOW)
    missions.set_status_directly(mission.mission_id, status)


@pytest.mark.asyncio
async def test_compute_unknown_robot_returns_none() -> None:
    svc = _svc(InMemoryRobotRepository(), InMemoryMissionRepository(), InMemoryRobotStateView())
    assert await svc.compute("ghost") is None


@pytest.mark.asyncio
async def test_compute_idle_when_online_without_executing_mission() -> None:
    robots = InMemoryRobotRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    assert (
        await _svc(robots, InMemoryMissionRepository(), InMemoryRobotStateView()).compute("r1")
        is RobotStatus.IDLE
    )


@pytest.mark.asyncio
async def test_compute_active_when_mission_executing() -> None:
    robots = InMemoryRobotRepository()
    missions = InMemoryMissionRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await _assign(missions, "r1", MissionStatus.RUNNING)
    assert (
        await _svc(robots, missions, InMemoryRobotStateView()).compute("r1") is RobotStatus.ACTIVE
    )


@pytest.mark.asyncio
async def test_compute_idle_when_mission_only_assigned() -> None:
    robots = InMemoryRobotRepository()
    missions = InMemoryMissionRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await _assign(missions, "r1", MissionStatus.ASSIGNED)  # assigned is not executing
    assert await _svc(robots, missions, InMemoryRobotStateView()).compute("r1") is RobotStatus.IDLE


@pytest.mark.asyncio
async def test_compute_offline_overrides_executing_mission() -> None:
    robots = InMemoryRobotRepository()
    missions = InMemoryMissionRepository()
    await robots.record_online("r1", Metadata(id="r1"))
    await robots.record_offline("r1")
    await _assign(missions, "r1", MissionStatus.RUNNING)
    assert (
        await _svc(robots, missions, InMemoryRobotStateView()).compute("r1") is RobotStatus.OFFLINE
    )


@pytest.mark.asyncio
async def test_compute_charging_when_battery_charging() -> None:
    robots = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    await robots.record_online("r1", Metadata(id="r1"))
    state_view.set_battery("r1", Battery(ts=_NOW, battery_pct=50, charging=True))
    assert (
        await _svc(robots, InMemoryMissionRepository(), state_view).compute("r1")
        is RobotStatus.CHARGING
    )
