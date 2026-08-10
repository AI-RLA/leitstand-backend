"""Unit tests for MissionManagementService using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.domain.errors import (
    InvalidMissionTransition,
    MissionDispatchTimeout,
    MissionRejectedByRobot,
    RobotBusy,
    RobotFactsheetMissing,
    StageNotHomogeneous,
    UnknownSite,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.mission import (
    MissionStatus,
    NavigationStage,
)
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    NavigationCapability,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.ports.inbound.mission_management import (
    CancelMissionCommand,
    CreateMissionCommand,
    DispatchMissionCommand,
    PauseMissionCommand,
    ResetMissionCommand,
    ResumeMissionCommand,
    UpdateMissionCommand,
)
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_site_repository import InMemorySiteRepository

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"
SITE_ID = UUID("11111111-1111-1111-1111-111111111111")


def _wgs84_stage() -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
    )


def _site_local_stage(site_id: UUID = SITE_ID) -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[SiteLocalWaypoint(site_id=site_id, x=1.0, y=0.5)],
    )


def _mixed_frame_stage() -> NavigationStage:
    return NavigationStage(
        stage_id=uuid4(),
        waypoints=[
            WGS84Waypoint(lat=52.3, lon=8.05),
            SiteLocalWaypoint(site_id=SITE_ID, x=1.0, y=0.5),
        ],
    )


def _factsheet(
    *,
    navigation: bool = True,
    frames: list[WaypointKind] | None = None,
) -> RobotFactsheet:
    nav = (
        NavigationCapability(
            supported_waypoint_kinds=(
                [WaypointKind.WGS84, WaypointKind.SITE_LOCAL] if frames is None else frames
            )
        )
        if navigation
        else None
    )
    return RobotFactsheet(robot_id=ROBOT_ID, navigation=nav)


def _make_svc():
    repo = InMemoryMissionRepository()
    dispatcher = FakeMissionDispatcher()
    factsheets = InMemoryRobotFactsheetView()
    events = InMemoryEventPublisher()
    sites = InMemorySiteRepository()
    sites.seed(SITE_ID)  # the site referenced by _site_local_stage exists in the catalog
    audit_calls: list[dict] = []

    async def audit(action, target_type, target_id, payload):
        audit_calls.append(
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            }
        )

    svc = MissionManagementService(
        repo=repo,
        dispatcher=dispatcher,
        factsheets=factsheets,
        events=events,
        audit=audit,
        sites=sites,
    )
    return svc, repo, dispatcher, factsheets, events, audit_calls


# ----------------------------------------------------------------------------
# create


@pytest.mark.asyncio
async def test_create_persists_mission_and_audits():
    svc, repo, _, _, _, audit_calls = _make_svc()
    cmd = CreateMissionCommand(name="m1", stages=[_wgs84_stage()])

    mission = await svc.create(cmd)

    assert mission.name == "m1"
    assert (await repo.get(mission.mission_id)) == mission
    assert await repo.get_status(mission.mission_id) is MissionStatus.DRAFT
    assert audit_calls[0]["action"] == "mission.create"


@pytest.mark.asyncio
async def test_create_rejects_mixed_frame_stage():
    svc, _, _, _, _, _ = _make_svc()
    with pytest.raises(StageNotHomogeneous):
        await svc.create(CreateMissionCommand(name="m1", stages=[_mixed_frame_stage()]))


@pytest.mark.asyncio
async def test_create_rejects_site_local_stage_referencing_unknown_site():
    svc, _, _, _, _, _ = _make_svc()
    unknown_site = uuid4()  # not in the catalog (only SITE_ID is seeded)
    with pytest.raises(UnknownSite):
        await svc.create(
            CreateMissionCommand(name="m1", stages=[_site_local_stage(site_id=unknown_site)])
        )


# ----------------------------------------------------------------------------
# update


@pytest.mark.asyncio
async def test_update_replaces_stages_on_draft():
    svc, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    new_stages = [_wgs84_stage(), _wgs84_stage()]
    updated = await svc.update(
        UpdateMissionCommand(mission_id=created.mission_id, stages=new_stages)
    )

    assert len(updated.stages) == 2


@pytest.mark.asyncio
async def test_update_rejects_non_draft():
    svc, repo, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(created.mission_id, MissionStatus.RUNNING)

    with pytest.raises(InvalidMissionTransition):
        await svc.update(UpdateMissionCommand(mission_id=created.mission_id, name="renamed"))


# ----------------------------------------------------------------------------
# dispatch


@pytest.mark.asyncio
async def test_dispatch_happy_path():
    svc, repo, dispatcher, factsheets, events, audit_calls = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    audit_calls.clear()

    result = await svc.dispatch(
        DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert await repo.get_status(mission.mission_id) is MissionStatus.DISPATCHED
    assert dispatcher.dispatched == [(mission, ROBOT_ID)]
    assert await repo.get_assigned_robot(mission.mission_id) == ROBOT_ID
    assert result.mission_id == mission.mission_id
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert lifecycle == [
        (
            f"events/mission/{mission.mission_id}/lifecycle",
            {
                "mission_id": str(mission.mission_id),
                "status": "DISPATCHED",
                "trigger": "dispatch",
            },
        )
    ]
    assert audit_calls[0]["action"] == "mission.dispatch"


@pytest.mark.asyncio
async def test_dispatch_refuses_terminal_mission():
    """A mission already terminal (e.g. cancelled just before dispatch) must not command the
    robot. The locked read makes the status the dispatch decides on authoritative, so a
    dispatch losing the race to a cancel refuses instead of driving a CANCELLED mission."""
    svc, repo, dispatcher, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.CANCELLED)

    with pytest.raises(InvalidMissionTransition):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    assert dispatcher.dispatched == []
    assert await repo.get_assigned_robot(mission.mission_id) is None


@pytest.mark.asyncio
async def test_dispatch_rejects_when_factsheet_missing():
    svc, _, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(RobotFactsheetMissing):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_when_robot_busy():
    svc, repo, _, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    first = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    second = await svc.create(CreateMissionCommand(name="m2", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=first.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(first.mission_id, MissionStatus.RUNNING)

    with pytest.raises(RobotBusy):
        await svc.dispatch(DispatchMissionCommand(mission_id=second.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_unsupported_stage_kind():
    svc, _, _, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet(navigation=False))
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(UnsupportedStageKind):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_unsupported_waypoint_frame():
    svc, _, _, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet(frames=[WaypointKind.SITE_LOCAL]))  # robot cannot do WGS84
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(UnsupportedWaypointFrame):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_marks_mission_failed_on_robot_reject():
    svc, repo, dispatcher, factsheets, events, audit_calls = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    audit_calls.clear()

    async def reject(_mission, robot_id):
        raise MissionRejectedByRobot(_mission.mission_id, robot_id, "manual rejection")

    dispatcher.set_dispatch_handler(reject)

    with pytest.raises(MissionRejectedByRobot):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    # The service marks the mission FAILED on dispatch failure; the dispatch
    # orchestrator commits this write before the error propagates (route -> 422).
    assert await repo.get_status(mission.mission_id) is MissionStatus.FAILED
    assert [t for t, _, _ in events.published if t.endswith("/dispatched")] == []
    assert [c for c in audit_calls if c["action"] == "mission.dispatch"] == []
    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is not None
    assert record.failure_errors[0].type == "dispatch_rejected"
    # The robot's reason is surfaced verbatim, without the str(exc) envelope.
    assert record.failure_errors[0].description == "manual rejection"


@pytest.mark.asyncio
async def test_dispatch_marks_mission_failed_on_timeout():
    svc, repo, dispatcher, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    async def timeout(_mission, robot_id):
        raise MissionDispatchTimeout(_mission.mission_id, robot_id)

    dispatcher.set_dispatch_handler(timeout)

    with pytest.raises(MissionDispatchTimeout):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    assert await repo.get_status(mission.mission_id) is MissionStatus.FAILED
    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is not None
    assert record.failure_errors[0].type == "dispatch_timeout"


# ----------------------------------------------------------------------------
# cancel / pause / resume


@pytest.mark.asyncio
async def test_cancel_draft_skips_dispatcher():
    svc, repo, dispatcher, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.CANCELLED
    assert dispatcher.cancelled == []


@pytest.mark.asyncio
async def test_cancel_running_calls_dispatcher():
    svc, repo, dispatcher, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.CANCELLED
    assert len(dispatcher.cancelled) == 1
    assert dispatcher.cancelled[0][0] == mission.mission_id
    assert dispatcher.cancelled[0][1] == ROBOT_ID


@pytest.mark.asyncio
async def test_pause_running_calls_dispatcher():
    svc, repo, dispatcher, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    await svc.pause(PauseMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.PAUSED
    assert dispatcher.paused == [(mission.mission_id, ROBOT_ID)]


@pytest.mark.asyncio
async def test_resume_paused_calls_dispatcher():
    svc, repo, dispatcher, factsheets, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(mission.mission_id, MissionStatus.PAUSED)

    await svc.resume(ResumeMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.RUNNING
    assert dispatcher.resumed == [(mission.mission_id, ROBOT_ID)]


@pytest.mark.asyncio
async def test_cancel_after_terminal_raises():
    svc, repo, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.SUCCEEDED)

    with pytest.raises(InvalidMissionTransition):
        await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))


@pytest.mark.asyncio
async def test_cancel_republishes_resolved_stage_state():
    """A cancel republishes the resolved per-stage view (latched), not clears it."""
    svc, repo, _, factsheets, events, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.CANCELLED
    # The stage never ran before the cancel, so it resolves to SKIPPED in the republished view.
    state_payloads = [p for t, p, latch in events.published if t.endswith("/state") and latch]
    assert state_payloads
    assert state_payloads[-1]["stage_states"][0]["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_reset_returns_mission_to_draft_and_emits_lifecycle():
    svc, repo, _, _, events, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.FAILED)

    await svc.reset(ResetMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.DRAFT
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert (
        f"events/mission/{mission.mission_id}/lifecycle",
        {"mission_id": str(mission.mission_id), "status": "DRAFT", "trigger": "reset"},
    ) in lifecycle
