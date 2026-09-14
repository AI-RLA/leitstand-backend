"""Reconciling a reconnected robot's live runs against what it reports."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from leitstand_backend.application.run_reconciliation import reconcile_robot_runs
from leitstand_backend.domain.model.mission.mission import NavigationStage
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository

ROBOT = "r1"
CONNECTED_AT = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _run(
    status: RunStatus,
    last_frame_at: datetime | None,
    created_at: datetime = CONNECTED_AT - timedelta(minutes=1),
) -> MissionRun:
    stage = NavigationStage(
        stage_id=uuid4(),
        name="go",
        waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
    )
    return MissionRun(
        run_id=uuid4(),
        mission_id=uuid4(),
        robot_id=ROBOT,
        status=status,
        stages=[stage],
        stages_digest="d",
        origin=RunOrigin(kind="manual", actor="tester"),
        created_at=created_at,
        updated_at=created_at,
        last_frame_at=last_frame_at,
    )


async def _reconcile(runs, dispatcher, events):
    return await reconcile_robot_runs(runs, dispatcher, events, ROBOT, CONNECTED_AT)


@pytest.mark.asyncio
async def test_a_run_the_robot_reports_since_reconnecting_is_left_alone():
    runs, dispatcher, events = (
        InMemoryMissionRunRepository(),
        FakeMissionDispatcher(),
        InMemoryEventPublisher(),
    )
    run = await runs.create(_run(RunStatus.RUNNING, CONNECTED_AT + timedelta(seconds=5)))

    assert await _reconcile(runs, dispatcher, events) == []
    assert (await runs.get(run.run_id)).status is RunStatus.RUNNING
    assert dispatcher.cancelled == []


@pytest.mark.asyncio
async def test_a_run_the_robot_never_claims_is_cancelled_then_failed():
    runs, dispatcher, events = (
        InMemoryMissionRunRepository(),
        FakeMissionDispatcher(),
        InMemoryEventPublisher(),
    )
    run = await runs.create(_run(RunStatus.RUNNING, CONNECTED_AT - timedelta(minutes=5)))

    assert await _reconcile(runs, dispatcher, events) == [run.run_id]
    settled = await runs.get(run.run_id)
    assert settled.status is RunStatus.FAILED
    assert settled.failure_errors[0].type == "run_unclaimed_after_reconnect"
    # The stop is sent before the record is closed, because the harmful mistake is a robot
    # that is still moving.
    assert [(rid, rid_robot) for rid, rid_robot, _ in dispatcher.cancelled] == [(run.run_id, ROBOT)]
    assert await runs.list_active_by_robot(ROBOT) == []


@pytest.mark.asyncio
async def test_a_run_that_never_reported_at_all_is_settled():
    runs, dispatcher, events = (
        InMemoryMissionRunRepository(),
        FakeMissionDispatcher(),
        InMemoryEventPublisher(),
    )
    run = await runs.create(_run(RunStatus.PENDING, None))

    assert await _reconcile(runs, dispatcher, events) == [run.run_id]
    assert (await runs.get(run.run_id)).status is RunStatus.FAILED


@pytest.mark.asyncio
async def test_a_finished_run_is_not_touched():
    runs, dispatcher, events = (
        InMemoryMissionRunRepository(),
        FakeMissionDispatcher(),
        InMemoryEventPublisher(),
    )
    run = await runs.create(_run(RunStatus.SUCCEEDED, None))

    assert await _reconcile(runs, dispatcher, events) == []
    assert (await runs.get(run.run_id)).status is RunStatus.SUCCEEDED
    assert dispatcher.cancelled == []


@pytest.mark.asyncio
async def test_a_run_started_after_the_robot_reconnected_is_out_of_scope():
    # The window this sweep runs in is long enough for an operator to dispatch inside it, and a
    # new run has no frames yet.
    runs, dispatcher, events = (
        InMemoryMissionRunRepository(),
        FakeMissionDispatcher(),
        InMemoryEventPublisher(),
    )
    run = await runs.create(
        _run(RunStatus.PENDING, None, created_at=CONNECTED_AT + timedelta(seconds=20))
    )

    assert await _reconcile(runs, dispatcher, events) == []
    assert (await runs.get(run.run_id)).status is RunStatus.PENDING
    assert dispatcher.cancelled == []
