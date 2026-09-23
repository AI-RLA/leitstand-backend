"""The two-transaction start, driven the way the route drives it, against Postgres.

Nothing hermetic exercises this driver: the route tests replace it, and the service tests call
its two halves on one fake. Here it runs both halves on real sessions with a fake robot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leitstand_backend.adapters.outbound.persistence.postgres.mission_repository_adapter import (
    PostgresMissionRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.mission_run_repository_adapter import (
    PostgresMissionRunRepositoryAdapter,
)
from leitstand_backend.domain.errors import MissionDispatchFailed, MissionRejectedByRobot
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    NavigationCapability,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.db import transactional_scope
from leitstand_backend.infrastructure.deps import _RunStartOrchestrator
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.inbound.run_management import StartRunCommand
from leitstand_backend.ports.outbound.event_publisher import mission_topic
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_NOW = datetime(2026, 9, 4, 6, 0, 0, tzinfo=timezone.utc)


async def _mission(session_factory: async_sessionmaker[AsyncSession]) -> Mission:
    mission = Mission(
        mission_id=uuid4(),
        name="orchestrated",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)])],
        created_at=_NOW,
        updated_at=_NOW,
    )
    async with transactional_scope(session_factory) as session:
        await PostgresMissionRepositoryAdapter(session).save(mission)
    return mission


async def _start(
    session_factory: async_sessionmaker[AsyncSession],
    dispatcher: FakeMissionDispatcher,
    mission: Mission,
    robot_id: str,
    bus: EventBus,
) -> MissionRun:
    factsheets = InMemoryRobotFactsheetView()
    factsheets.set(
        RobotFactsheet(
            robot_id=robot_id,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        )
    )
    orchestrator = _RunStartOrchestrator(
        session_factory=session_factory,
        dispatcher=dispatcher,
        factsheets=factsheets,
        bus=bus,
        current_user=User(id="operator", name="Operator"),
    )
    return await orchestrator.start(
        StartRunCommand(
            mission_id=mission.mission_id,
            robot_id=robot_id,
            origin=RunOrigin(kind="manual", actor="human"),
        )
    )


async def test_an_accepted_dispatch_commits_pending_then_dispatched(db_session_factory) -> None:
    mission = await _mission(db_session_factory)
    robot = f"robot-{uuid4()}"
    bus = EventBus()
    dispatcher = FakeMissionDispatcher()
    run = await _start(db_session_factory, dispatcher, mission, robot, bus)
    assert run.status is RunStatus.DISPATCHED
    assert run.dispatched_at is not None
    assert dispatcher.dispatched[0][0] == run.run_id
    # Committed, not just in a session: a fresh session sees it.
    async with transactional_scope(db_session_factory) as session:
        stored = await PostgresMissionRunRepositoryAdapter(session).get(run.run_id)
    assert stored is not None and stored.status is RunStatus.DISPATCHED
    # The all-WAITING baseline reached the bus after the first commit.
    latched = bus.latched(mission_topic(mission.mission_id, "state"))
    assert latched is not None and latched["run_id"] == str(run.run_id)
    assert [s["status"] for s in latched["stage_states"]] == ["WAITING"]


async def test_a_rejected_dispatch_is_durable_and_the_error_propagates(db_session_factory) -> None:
    mission = await _mission(db_session_factory)
    robot = f"robot-{uuid4()}"
    dispatcher = FakeMissionDispatcher()

    async def reject(run_id, stages, robot_id):
        raise MissionRejectedByRobot(run_id, robot_id, "nav stack down")

    dispatcher.set_dispatch_handler(reject)
    with pytest.raises(MissionRejectedByRobot):
        await _start(db_session_factory, dispatcher, mission, robot, EventBus())
    async with transactional_scope(db_session_factory) as session:
        runs = PostgresMissionRunRepositoryAdapter(session)
        (run,) = await runs.list_by_mission(mission.mission_id)
        full = await runs.get(run.run_id)
    assert run.status is RunStatus.REJECTED
    assert full.failure_errors[0].description == "nav stack down"


async def test_a_transport_failure_leaves_the_run_live_and_its_robot_held(
    db_session_factory,
) -> None:
    mission = await _mission(db_session_factory)
    robot = f"robot-{uuid4()}"
    dispatcher = FakeMissionDispatcher()

    async def explode(run_id, stages, robot_id):
        raise RuntimeError("zenoh session closed")

    dispatcher.set_dispatch_handler(explode)
    with pytest.raises(MissionDispatchFailed):
        await _start(db_session_factory, dispatcher, mission, robot, EventBus())
    async with transactional_scope(db_session_factory) as session:
        runs = PostgresMissionRunRepositoryAdapter(session)
        (run,) = await runs.list_by_mission(mission.mission_id)
        # A transport that failed says nothing about the machine, so the run stays live and
        # keeps its robot until the robot itself settles it.
        assert run.status is RunStatus.PENDING
        assert [r.run_id for r in await runs.list_active_by_robot(robot)] == [run.run_id]
