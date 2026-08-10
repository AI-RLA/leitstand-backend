"""Unit tests for FleetViewService."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from leitstand_backend.application.fleet_view_service import FleetViewService
from leitstand_backend.domain.model.mission.mission import Mission, MissionStatus, NavigationStage
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery, Pose
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_robot_state_view import InMemoryRobotStateView

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_svc(
    repo: InMemoryRobotRepository,
    state_view: InMemoryRobotStateView | None = None,
    missions: InMemoryMissionRepository | None = None,
) -> FleetViewService:
    return FleetViewService(
        repo=repo,
        state_view=state_view or InMemoryRobotStateView(),
        missions=missions or InMemoryMissionRepository(),
    )


def _mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_get_robot_returns_overview_with_telemetry() -> None:
    repo = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    svc = _make_svc(repo, state_view)

    await repo.record_online("r1", Metadata(id="r1"))
    pose = Pose(ts=_NOW, lat=50.78, lon=7.18, heading_deg=90.0)
    state_view.set_pose("r1", pose)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.robot.id == "r1"
    assert overview.pose == pose
    assert overview.battery is None
    # Online, no executing mission -> idle.
    assert overview.status == RobotStatus.IDLE


@pytest.mark.asyncio
async def test_get_robot_returns_none_for_unknown_id() -> None:
    assert await _make_svc(InMemoryRobotRepository()).get_robot("ghost") is None


@pytest.mark.asyncio
async def test_list_robots_returns_all_overviews() -> None:
    repo = InMemoryRobotRepository()
    svc = _make_svc(repo)

    await repo.record_online("r1", Metadata(id="r1"))
    await repo.record_online("r2", Metadata(id="r2"))

    overviews = await svc.list_robots()

    assert len(overviews) == 2
    assert {o.robot.id for o in overviews} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_overview_reflects_battery() -> None:
    repo = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    svc = _make_svc(repo, state_view)

    await repo.record_online("r1", Metadata(id="r1"))
    battery = Battery(ts=_NOW, battery_pct=75, charging=False)
    state_view.set_battery("r1", battery)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.battery == battery
    assert overview.pose is None


@pytest.mark.asyncio
async def test_status_active_when_robot_has_executing_mission() -> None:
    repo = InMemoryRobotRepository()
    missions = InMemoryMissionRepository()
    svc = _make_svc(repo, missions=missions)

    await repo.record_online("r1", Metadata(id="r1"))
    mission = _mission()
    await missions.save(mission)
    await missions.assign_robot(mission.mission_id, "r1", _NOW)
    missions.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.status == RobotStatus.ACTIVE


@pytest.mark.asyncio
async def test_status_offline_overrides_executing_mission() -> None:
    repo = InMemoryRobotRepository()
    missions = InMemoryMissionRepository()
    svc = _make_svc(repo, missions=missions)

    await repo.record_online("r1", Metadata(id="r1"))
    await repo.record_offline("r1")
    mission = _mission()
    await missions.save(mission)
    await missions.assign_robot(mission.mission_id, "r1", _NOW)
    missions.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.status == RobotStatus.OFFLINE
