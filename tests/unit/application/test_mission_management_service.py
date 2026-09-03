"""Unit tests for MissionManagementService using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from geojson_pydantic import Polygon

from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.domain.errors import (
    GeneratedPlanNotEditable,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionDispatchTimeout,
    MissionRejectedByRobot,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    StageNotHomogeneous,
    StaleCoverageBoundary,
    UnknownSite,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.field import Field
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoverageParams,
    CoverageProvenance,
    boundary_digest,
)
from leitstand_backend.domain.model.mission.mission import MissionStatus
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.ports.inbound.mission_management import (
    AssignMissionCommand,
    CancelMissionCommand,
    CoverageStageInput,
    CreateMissionCommand,
    DispatchMissionCommand,
    NavigationStageInput,
    PauseMissionCommand,
    ResetMissionCommand,
    ResumeMissionCommand,
    SegmentInput,
    UpdateMissionCommand,
)
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_site_repository import InMemorySiteRepository

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"
SITE_ID = UUID("11111111-1111-1111-1111-111111111111")


def _wgs84_stage() -> NavigationStageInput:
    return NavigationStageInput(waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])


def _site_local_stage(site_id: UUID = SITE_ID) -> NavigationStageInput:
    return NavigationStageInput(waypoints=[SiteLocalWaypoint(site_id=site_id, x=1.0, y=0.5)])


def _mixed_frame_stage() -> NavigationStageInput:
    return NavigationStageInput(
        waypoints=[
            WGS84Waypoint(lat=52.3, lon=8.05),
            SiteLocalWaypoint(site_id=SITE_ID, x=1.0, y=0.5),
        ]
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


def _coverage_factsheet(min_turning_radius_m: float, track_width_m: float = 1.0) -> RobotFactsheet:
    return RobotFactsheet(
        robot_id=ROBOT_ID,
        navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        physical_parameters=PhysicalParameters(
            track_width_m=track_width_m, min_turning_radius_m=min_turning_radius_m
        ),
    )


async def _coverage_mission(svc):
    wps = [WGS84Waypoint(lat=52.0, lon=8.0), WGS84Waypoint(lat=52.001, lon=8.0)]
    return await svc.create(
        CreateMissionCommand(
            name="coverage",
            description=None,
            stages=[CoverageStageInput(segments=[SegmentInput(kind="swath", waypoints=wps)])],
        )
    )


_FIELD_POLYGON = Polygon(
    type="Polygon",
    coordinates=[[(8.0, 52.0), (8.001, 52.0), (8.001, 52.001), (8.0, 52.001), (8.0, 52.0)]],
)


def _seed_field(fields: InMemoryFieldRepository) -> Field:
    field = Field(
        id=uuid4(),
        name="north",
        geometry=_FIELD_POLYGON,
        area_ha=1.0,
        notes=None,
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )
    fields.seed(field)
    return field


def _provenance(turning_radius_m: float, field: Field) -> CoverageProvenance:
    return CoverageProvenance(
        field_id=field.id,
        boundary_digest=boundary_digest(field.geometry),
        field_area_m2=10_000.0,
        params=CoverageParams(
            operation_width_m=3.0,
            turning_radius_m=turning_radius_m,
            headland_width_m=turning_radius_m,
        ),
        metrics=CoverageMetrics(swath_count=3, track_length_m=300.0, covered_area_m2=900.0),
        planner_version="fake 1",
        planned_for_robot_id=ROBOT_ID,
        planned_at=UTC_NOW,
    )


async def _seeded_coverage(svc, repo, fields, radius_planned_for: float):
    """Create a coverage mission whose plan was laid out for the given turning radius."""
    field = _seed_field(fields)
    mission = await _coverage_mission(svc)
    await repo.save_coverage_provenance(mission.mission_id, _provenance(radius_planned_for, field))
    return mission


def _make_svc():
    repo = InMemoryMissionRepository()
    dispatcher = FakeMissionDispatcher()
    factsheets = InMemoryRobotFactsheetView()
    events = InMemoryEventPublisher()
    sites = InMemorySiteRepository()
    fields = InMemoryFieldRepository()
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
        fields=fields,
    )
    return svc, repo, dispatcher, factsheets, events, audit_calls, fields


# ----------------------------------------------------------------------------
# create


@pytest.mark.asyncio
async def test_create_persists_mission_and_audits():
    svc, repo, _, _, _, audit_calls, _ = _make_svc()
    cmd = CreateMissionCommand(name="m1", stages=[_wgs84_stage()])

    mission = await svc.create(cmd)

    assert mission.name == "m1"
    assert (await repo.get(mission.mission_id)) == mission
    assert await repo.get_status(mission.mission_id) is MissionStatus.DRAFT
    assert audit_calls[0]["action"] == "mission.create"


@pytest.mark.asyncio
async def test_create_assigns_every_stage_a_distinct_id():
    """Stage identity is the backend's to assign, and it must be unique per stage.

    ``mission_stage_state`` is keyed ``(mission_id, stage_id)``, so two stages sharing an id
    collapse onto one row: the second robot report silently overwrites the first and neither
    stage can be told apart afterwards. Nothing validates against that, so the guarantee has to
    come from ids never being chosen by a caller.
    """
    svc, _, _, _, _, _, _ = _make_svc()

    mission = await svc.create(
        CreateMissionCommand(name="m1", stages=[_wgs84_stage(), _wgs84_stage()])
    )

    ids = [stage.stage_id for stage in mission.stages]
    assert len(set(ids)) == 2


@pytest.mark.asyncio
async def test_a_caller_cannot_choose_a_stage_id():
    """The property the whole split exists for, asserted from the wire shape inwards.

    A caller that supplies stage_id gets it ignored rather than honoured, so no client can
    reserve, collide with, or overwrite a stage's identity.
    """
    svc, _, _, _, _, _, _ = _make_svc()
    chosen = uuid4()
    cmd = CreateMissionCommand.model_validate(
        {
            "name": "m1",
            "stages": [
                {
                    "kind": "navigation",
                    "stage_id": str(chosen),
                    "waypoints": [{"kind": "wgs84", "lat": 52.3, "lon": 8.05}],
                }
            ],
        }
    )

    mission = await svc.create(cmd)

    assert mission.stages[0].stage_id != chosen


@pytest.mark.asyncio
async def test_create_assigns_ids_to_cleanup_stages_too():
    """on_cancel stages are stages: the robot reports them under the same stage_id join."""
    svc, _, _, _, _, _, _ = _make_svc()
    stage = NavigationStageInput(
        waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
        on_cancel=[NavigationStageInput(waypoints=[WGS84Waypoint(lat=52.4, lon=8.06)])],
    )

    mission = await svc.create(CreateMissionCommand(name="m1", stages=[stage]))

    cleanup = mission.stages[0].on_cancel
    assert cleanup is not None
    assert cleanup[0].stage_id != mission.stages[0].stage_id


@pytest.mark.asyncio
async def test_update_assigns_ids_to_replacement_stages():
    svc, _, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    updated = await svc.update(
        UpdateMissionCommand(mission_id=created.mission_id, stages=[_wgs84_stage()])
    )

    assert isinstance(updated.stages[0].stage_id, UUID)


@pytest.mark.asyncio
async def test_create_rejects_mixed_frame_stage():
    svc, _, _, _, _, _, _ = _make_svc()
    with pytest.raises(StageNotHomogeneous):
        await svc.create(CreateMissionCommand(name="m1", stages=[_mixed_frame_stage()]))


@pytest.mark.asyncio
async def test_a_rejected_stage_is_identified_by_position():
    """A rejected request has no server-assigned ids, so position is all the caller can act on."""
    svc, _, _, _, _, _, _ = _make_svc()

    with pytest.raises(StageNotHomogeneous) as raised:
        await svc.create(
            CreateMissionCommand(name="m1", stages=[_wgs84_stage(), _mixed_frame_stage()])
        )

    assert raised.value.stage_index == 1


@pytest.mark.asyncio
async def test_create_rejects_site_local_stage_referencing_unknown_site():
    svc, _, _, _, _, _, _ = _make_svc()
    unknown_site = uuid4()  # not in the catalog (only SITE_ID is seeded)
    with pytest.raises(UnknownSite):
        await svc.create(
            CreateMissionCommand(name="m1", stages=[_site_local_stage(site_id=unknown_site)])
        )


# ----------------------------------------------------------------------------
# update


@pytest.mark.asyncio
async def test_update_replaces_stages_on_draft():
    svc, _, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    new_stages = [_wgs84_stage(), _wgs84_stage()]
    updated = await svc.update(
        UpdateMissionCommand(mission_id=created.mission_id, stages=new_stages)
    )

    assert len(updated.stages) == 2


@pytest.mark.asyncio
async def test_update_rejects_non_draft():
    svc, repo, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(created.mission_id, MissionStatus.RUNNING)

    with pytest.raises(InvalidMissionTransition):
        await svc.update(UpdateMissionCommand(mission_id=created.mission_id, name="renamed"))


# ----------------------------------------------------------------------------
# dispatch


@pytest.mark.asyncio
async def test_dispatch_happy_path():
    svc, repo, dispatcher, factsheets, events, audit_calls, _ = _make_svc()
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
    svc, repo, dispatcher, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.CANCELLED)

    with pytest.raises(InvalidMissionTransition):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    assert dispatcher.dispatched == []
    assert await repo.get_assigned_robot(mission.mission_id) is None


@pytest.mark.asyncio
async def test_dispatch_rejects_when_factsheet_missing():
    svc, _, _, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(RobotFactsheetMissing):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_when_robot_busy():
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_factsheet())
    first = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    second = await svc.create(CreateMissionCommand(name="m2", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=first.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(first.mission_id, MissionStatus.RUNNING)

    with pytest.raises(RobotBusy):
        await svc.dispatch(DispatchMissionCommand(mission_id=second.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_unsupported_stage_kind():
    svc, _, _, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet(navigation=False))
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(UnsupportedStageKind):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_rejects_unsupported_waypoint_frame():
    svc, _, _, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet(frames=[WaypointKind.SITE_LOCAL]))  # robot cannot do WGS84
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    with pytest.raises(UnsupportedWaypointFrame):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_marks_mission_failed_on_robot_reject():
    svc, repo, dispatcher, factsheets, events, audit_calls, _ = _make_svc()
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
    # The refusal is a state change and has to leave a record of its own: auditing only successes
    # would leave the FAILED mission, its robot assignment and its error with nothing naming who
    # caused them, which is the direction an incident review reads.
    (failed,) = [c for c in audit_calls if c["action"] == "mission.dispatch_failed"]
    assert failed["target_id"] == str(mission.mission_id)
    assert failed["payload"]["robot_id"] == ROBOT_ID
    assert failed["payload"]["error_type"] == "dispatch_rejected"
    assert failed["payload"]["reason"] == "manual rejection"
    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is not None
    assert record.failure_errors[0].type == "dispatch_rejected"
    # The robot's reason is surfaced verbatim, without the str(exc) envelope.
    assert record.failure_errors[0].description == "manual rejection"


@pytest.mark.asyncio
async def test_dispatch_marks_mission_failed_on_timeout():
    svc, repo, dispatcher, factsheets, _, audit_calls, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    audit_calls.clear()

    async def timeout(_mission, robot_id):
        raise MissionDispatchTimeout(_mission.mission_id, robot_id)

    dispatcher.set_dispatch_handler(timeout)

    with pytest.raises(MissionDispatchTimeout):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    assert await repo.get_status(mission.mission_id) is MissionStatus.FAILED
    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is not None
    assert record.failure_errors[0].type == "dispatch_timeout"
    (failed,) = [c for c in audit_calls if c["action"] == "mission.dispatch_failed"]
    assert failed["payload"]["error_type"] == "dispatch_timeout"


# ----------------------------------------------------------------------------
# cancel / pause / resume


@pytest.mark.asyncio
async def test_cancel_draft_skips_dispatcher():
    svc, repo, dispatcher, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.CANCELLED
    assert dispatcher.cancelled == []


@pytest.mark.asyncio
async def test_cancel_running_calls_dispatcher():
    svc, repo, dispatcher, factsheets, _, _, _ = _make_svc()
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
    svc, repo, dispatcher, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    await svc.pause(PauseMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.PAUSED
    assert dispatcher.paused == [(mission.mission_id, ROBOT_ID)]


@pytest.mark.asyncio
async def test_resume_paused_calls_dispatcher():
    svc, repo, dispatcher, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    repo.set_status_directly(mission.mission_id, MissionStatus.PAUSED)

    await svc.resume(ResumeMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.RUNNING
    assert dispatcher.resumed == [(mission.mission_id, ROBOT_ID)]


@pytest.mark.asyncio
async def test_cancel_after_terminal_raises():
    svc, repo, _, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.SUCCEEDED)

    with pytest.raises(InvalidMissionTransition):
        await svc.cancel(CancelMissionCommand(mission_id=mission.mission_id))


@pytest.mark.asyncio
async def test_cancel_republishes_resolved_stage_state():
    """A cancel republishes the resolved per-stage view (latched), not clears it."""
    svc, repo, _, factsheets, events, _, _ = _make_svc()
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
    svc, repo, _, _, events, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    repo.set_status_directly(mission.mission_id, MissionStatus.FAILED)

    await svc.reset(ResetMissionCommand(mission_id=mission.mission_id))

    assert await repo.get_status(mission.mission_id) is MissionStatus.DRAFT
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert (
        f"events/mission/{mission.mission_id}/lifecycle",
        {"mission_id": str(mission.mission_id), "status": "DRAFT", "trigger": "reset"},
    ) in lifecycle


@pytest.mark.asyncio
async def test_coverage_is_refused_to_a_robot_that_only_drives_points():
    """Declaring navigation is not declaring coverage.

    A robot that visits points would drive a swath as two goals and take any convenient route
    between them, leaving the ground beside the line unworked. That is invisible in telemetry, so
    it has to be refused before dispatch rather than noticed after.
    """
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    mission = await svc.create(
        CreateMissionCommand(
            name="cover",
            stages=[
                CoverageStageInput(
                    segments=[
                        SegmentInput(
                            kind="swath",
                            waypoints=[
                                WGS84Waypoint(lat=52.3, lon=8.05),
                                WGS84Waypoint(lat=52.31, lon=8.05),
                            ],
                        )
                    ],
                )
            ],
        )
    )
    factsheets.set(_factsheet(frames=[WaypointKind.WGS84]))

    with pytest.raises(UnsupportedStageKind):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_coverage_is_accepted_by_a_robot_that_declares_it():
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    mission = await svc.create(
        CreateMissionCommand(
            name="cover",
            stages=[
                CoverageStageInput(
                    segments=[
                        SegmentInput(
                            kind="swath",
                            waypoints=[
                                WGS84Waypoint(lat=52.3, lon=8.05),
                                WGS84Waypoint(lat=52.31, lon=8.05),
                            ],
                        )
                    ],
                )
            ],
        )
    )
    factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        )
    )

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_assign_refuses_a_robot_that_turns_wider_than_the_plan():
    """The plan is laid out for one machine's turns and the robot running it need not be that one.

    A machine that cannot turn as tightly cuts every corner at every swath end, and nothing
    downstream reports that as anything but a mission driven slightly oddly.
    """
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=0.0)

    with pytest.raises(IncompatibleTurningRadius):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_assign_refuses_a_robot_wider_than_the_implement_the_plan_was_spaced_for():
    """Swath spacing follows the implement, so a machine wider than it overruns worked ground.

    Fields2Cover refuses this geometry at plan time; nothing re-checked it when the plan was
    handed to a different machine, and the wheels leave no evidence in any status the backend has.
    """
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5, track_width_m=4.0))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    with pytest.raises(ImplementNarrowerThanRobot):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_assign_allows_a_robot_narrower_than_the_implement():
    """The implement is normally wider than the machine carrying it, which is the ordinary case."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5, track_width_m=2.0))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_assign_allows_a_robot_that_turns_tighter_than_the_plan():
    """Wider turns than needed are drivable; the guard is one-directional on purpose."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_dispatch_rechecks_kinematics_against_the_current_declaration():
    """A robot re-registering between assign and dispatch replaces what assign decided against."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))

    factsheets.set(_coverage_factsheet(2.0))

    with pytest.raises(IncompatibleTurningRadius):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id))


@pytest.mark.asyncio
async def test_a_hand_authored_mission_is_not_kinematically_gated():
    """Only a generated plan states a radius it was laid out for; a typed one asserts nothing."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(9.0))
    mission = await _coverage_mission(svc)

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_assign_refuses_a_robot_that_declares_no_physical_parameters():
    """A machine that has not said how it turns cannot be shown to be able to drive the plan."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        )
    )
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    with pytest.raises(RobotPhysicalParametersMissing):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_assign_refuses_a_plan_whose_field_has_been_redrawn():
    """The path is frozen on the mission while the field it came from stays editable."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    field_id = (await repo.get_record(mission.mission_id)).coverage.field_id
    await fields.update(
        field_id,
        geometry=Polygon(
            type="Polygon",
            coordinates=[[(8.0, 52.0), (8.002, 52.0), (8.002, 52.002), (8.0, 52.002), (8.0, 52.0)]],
        ),
    )

    with pytest.raises(StaleCoverageBoundary):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_dispatch_refuses_a_plan_whose_field_has_been_deleted():
    """A field carries no history, so a deleted one leaves the plan describing nothing at all."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))
    await fields.delete((await repo.get_record(mission.mission_id)).coverage.field_id)

    with pytest.raises(StaleCoverageBoundary):
        await svc.dispatch(DispatchMissionCommand(mission_id=mission.mission_id))


@pytest.mark.asyncio
async def test_a_mission_without_a_plan_never_reads_the_field_catalog():
    """Only a generated plan names a field; a typed mission must not pay for the lookup."""
    svc, repo, _, factsheets, _, _, _ = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _coverage_mission(svc)

    class _Explodes:
        async def get(self, field_id):
            raise AssertionError("the field catalog was read for a mission with no plan")

    svc._fields = _Explodes()

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_replacing_the_stages_of_a_planned_mission_is_refused():
    """Provenance would survive the geometry it describes, and every guard reads it."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    with pytest.raises(GeneratedPlanNotEditable):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[NavigationStageInput(waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)])],
            )
        )


@pytest.mark.asyncio
async def test_renaming_a_planned_mission_is_still_allowed():
    """Only the geometry is derived, so the name stays the operator's to change."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    renamed = await svc.update(
        UpdateMissionCommand(mission_id=mission.mission_id, name="Nordfeld Vormittag")
    )

    assert renamed.name == "Nordfeld Vormittag"


@pytest.mark.asyncio
async def test_a_site_named_only_in_the_path_is_still_checked():
    """The path is driven and a caller can supply one, so its waypoints cannot skip the checks.

    Nothing else would catch this: the swaths name a site that exists, so only reading the path
    reveals the one that does not.
    """
    svc, repo, _, _, _, _, _ = _make_svc()
    good = [
        SiteLocalWaypoint(site_id=SITE_ID, x=0.0, y=0.0),
        SiteLocalWaypoint(site_id=SITE_ID, x=1.0, y=0.0),
    ]

    with pytest.raises(UnknownSite):
        await svc.create(
            CreateMissionCommand(
                name="smuggled",
                description=None,
                stages=[
                    CoverageStageInput(
                        segments=[
                            SegmentInput(kind="swath", waypoints=good),
                            SegmentInput(
                                kind="turn",
                                waypoints=[
                                    SiteLocalWaypoint(site_id=uuid4(), x=1.0, y=2.0),
                                    SiteLocalWaypoint(site_id=uuid4(), x=3.0, y=4.0),
                                ],
                            ),
                        ],
                    )
                ],
            )
        )
