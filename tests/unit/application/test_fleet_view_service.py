"""Unit tests for FleetViewService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leitstand_backend.application.fleet_view_service import FleetViewService
from leitstand_backend.domain.model.robot import Metadata
from leitstand_backend.domain.model.telemetry import Battery, Pose
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_robot_state_view import InMemoryRobotStateView

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_get_robot_returns_overview_with_telemetry() -> None:
    repo = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    svc = FleetViewService(repo=repo, state_view=state_view)

    await repo.record_online("r1", Metadata(id="r1"))
    pose = Pose(ts=_NOW, lat=50.78, lon=7.18, heading_deg=90.0)
    state_view.set_pose("r1", pose)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.robot.id == "r1"
    assert overview.pose == pose
    assert overview.battery is None
    assert overview.state is None


@pytest.mark.asyncio
async def test_get_robot_returns_none_for_unknown_id() -> None:
    svc = FleetViewService(repo=InMemoryRobotRepository(), state_view=InMemoryRobotStateView())
    assert await svc.get_robot("ghost") is None


@pytest.mark.asyncio
async def test_list_robots_returns_all_overviews() -> None:
    repo = InMemoryRobotRepository()
    svc = FleetViewService(repo=repo, state_view=InMemoryRobotStateView())

    await repo.record_online("r1", Metadata(id="r1"))
    await repo.record_online("r2", Metadata(id="r2"))

    overviews = await svc.list_robots()

    assert len(overviews) == 2
    assert {o.robot.id for o in overviews} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_overview_reflects_battery_and_state() -> None:
    repo = InMemoryRobotRepository()
    state_view = InMemoryRobotStateView()
    svc = FleetViewService(repo=repo, state_view=state_view)

    await repo.record_online("r1", Metadata(id="r1"))
    battery = Battery(ts=_NOW, battery_pct=75, charging=False)
    state_view.set_battery("r1", battery)

    overview = await svc.get_robot("r1")

    assert overview is not None
    assert overview.battery == battery
    assert overview.pose is None
