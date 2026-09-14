"""Unit tests for FleetViewService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leitstand_backend.application.fleet_view_service import FleetViewService
from leitstand_backend.domain.model.mission.mission_run import MissionRun
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_factsheet import (
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery, Pose
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_robot_state_view import InMemoryRobotStateView
from tests.fakes.runs import mission_run

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_svc(
    repo: InMemoryRobotRepository,
    state_view: InMemoryRobotStateView | None = None,
    runs: InMemoryMissionRunRepository | None = None,
    factsheets: InMemoryRobotFactsheetView | None = None,
) -> FleetViewService:
    return FleetViewService(
        repo=repo,
        state_view=state_view or InMemoryRobotStateView(),
        runs=runs or InMemoryMissionRunRepository(),
        factsheets=factsheets or InMemoryRobotFactsheetView(),
    )


def _run(robot_id: str, status: RunStatus) -> MissionRun:
    return mission_run(robot_id=robot_id, status=status)


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
    runs = InMemoryMissionRunRepository()
    svc = _make_svc(repo, runs=runs)

    await repo.record_online("r1", Metadata(id="r1"))
    run = await runs.create(_run("r1", RunStatus.RUNNING))

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.status == RobotStatus.ACTIVE
    assert overview.current_run is not None and overview.current_run.run_id == run.run_id


@pytest.mark.asyncio
async def test_status_offline_overrides_executing_mission() -> None:
    repo = InMemoryRobotRepository()
    runs = InMemoryMissionRunRepository()
    svc = _make_svc(repo, runs=runs)

    await repo.record_online("r1", Metadata(id="r1"))
    await repo.record_offline("r1")
    await runs.create(_run("r1", RunStatus.RUNNING))

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.status == RobotStatus.OFFLINE


@pytest.mark.asyncio
async def test_overview_carries_the_robots_declared_factsheet():
    """The backend refuses work on the strength of it, so an operator must be able to read it."""
    repo = InMemoryRobotRepository()
    await repo.record_online("r1", Metadata(id="r1"))
    factsheets = InMemoryRobotFactsheetView()
    factsheets.set(
        RobotFactsheet(
            robot_id="r1",
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(track_width_m=0.58, min_turning_radius_m=0.0),
        )
    )

    overview = await _make_svc(repo, factsheets=factsheets).get_robot("r1")

    assert overview is not None
    assert overview.factsheet is not None
    assert overview.factsheet.physical_parameters.track_width_m == 0.58


@pytest.mark.asyncio
async def test_a_robot_that_declared_nothing_carries_no_factsheet():
    repo = InMemoryRobotRepository()
    await repo.record_online("r2", Metadata(id="r2"))

    overview = await _make_svc(repo).get_robot("r2")

    assert overview is not None
    assert overview.factsheet is None
