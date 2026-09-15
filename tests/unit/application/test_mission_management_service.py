"""Unit tests for MissionManagementService (definitions only) using in-memory fakes.

Dispatch, cancel, pause and resume live in test_run_service.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from geojson_pydantic import Polygon

from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.domain.errors import (
    CoveragePlannerUnavailable,
    DuplicateStageId,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    MissionArchived,
    MissionNotArchived,
    MissionRunInProgress,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    StageNotHomogeneous,
    StageNotInMission,
    StageSpansSites,
    StaleCoverageBoundary,
    UnknownSite,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.mission.mission import CoverageStage, Segment
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
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
    CoverageStageInput,
    CreateGeneratedMissionCommand,
    CreateMissionCommand,
    DeleteMissionCommand,
    NavigationStageInput,
    RestoreMissionCommand,
    UnassignMissionCommand,
    UpdateMissionCommand,
)
from tests.fakes.coverage_stage_planning import bind_stage_planning
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_site_repository import InMemorySiteRepository
from tests.fakes.planned_coverage import (
    coverage_provenance,
    coverage_stage,
    coverage_stage_for,
    seeded_field,
)

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"
SITE_ID = UUID("11111111-1111-1111-1111-111111111111")
SITE_B = UUID("22222222-2222-2222-2222-222222222222")


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


async def _coverage_mission(svc, **stage_kwargs):
    return await svc.create_generated(
        CreateGeneratedMissionCommand(
            name="coverage",
            description=None,
            stages=[coverage_stage(**stage_kwargs)],
        )
    )


async def _seeded_coverage(svc, repo, fields, radius_planned_for: float):
    """A coverage mission whose path was laid out for the given turning radius, field seeded."""
    field = seeded_field(fields)
    return await svc.create_generated(
        CreateGeneratedMissionCommand(
            name="coverage",
            description=None,
            stages=[coverage_stage_for(field, turning_radius_m=radius_planned_for)],
        )
    )


def _make_svc():
    repo = InMemoryMissionRepository()
    runs = InMemoryMissionRunRepository()
    factsheets = InMemoryRobotFactsheetView()
    events = InMemoryEventPublisher()
    sites = InMemorySiteRepository()
    fields = InMemoryFieldRepository()
    sites.seed(SITE_ID)  # the site referenced by _site_local_stage exists in the catalog
    sites.seed(SITE_B)
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

    plan, unchanged, planner = bind_stage_planning(fields, factsheets)
    svc = MissionManagementService(
        repo=repo,
        runs=runs,
        factsheets=factsheets,
        events=events,
        audit=audit,
        sites=sites,
        fields=fields,
        planner=plan,
        unchanged=unchanged,
    )
    svc.fake_planner = planner  # type: ignore[attr-defined]
    return svc, repo, runs, factsheets, events, audit_calls, fields


async def _run_of(
    runs: InMemoryMissionRunRepository, mission, status: RunStatus, robot_id=ROBOT_ID
):
    """Seed a run directly; how one starts is the run service's concern."""
    return await runs.create(
        MissionRun(
            run_id=uuid4(),
            mission_id=mission.mission_id,
            robot_id=robot_id,
            status=status,
            stages=mission.stages,
            stages_digest=stages_digest(mission.stages),
            origin=RunOrigin(kind="manual", actor="human"),
            created_at=UTC_NOW,
            updated_at=UTC_NOW,
        )
    )


# create


@pytest.mark.asyncio
async def test_create_persists_mission_and_audits():
    svc, repo, runs, _, _, audit_calls, _ = _make_svc()
    cmd = CreateMissionCommand(name="m1", stages=[_wgs84_stage()])

    mission = await svc.create(cmd)

    assert mission.name == "m1"
    assert (await repo.get(mission.mission_id)) == mission
    assert (await runs.list_by_mission(mission.mission_id)) == []
    assert audit_calls[0]["action"] == "mission.create"


@pytest.mark.asyncio
async def test_create_assigns_every_stage_a_distinct_id():
    svc, _, _, _, _, _, _ = _make_svc()
    mission = await svc.create(
        CreateMissionCommand(name="m1", stages=[_wgs84_stage(), _wgs84_stage()])
    )
    ids = [stage.stage_id for stage in mission.stages]
    assert len(set(ids)) == 2


@pytest.mark.asyncio
async def test_a_new_mission_refuses_a_supplied_stage_id():
    """There is nothing to keep on a new mission, so a supplied id can only be a mistake."""
    svc, _, _, _, _, _, _ = _make_svc()
    with pytest.raises(StageNotInMission):
        await svc.create(
            CreateMissionCommand(
                name="m1",
                stages=[
                    NavigationStageInput(
                        stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)]
                    )
                ],
            )
        )


@pytest.mark.asyncio
async def test_an_edit_keeps_the_ids_it_names_and_assigns_the_rest():
    """Stable ids across edits are what make runs before and after comparable stage by stage."""
    svc, _, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    kept = created.stages[0].stage_id

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=created.mission_id,
            stages=[
                NavigationStageInput(stage_id=kept, waypoints=[WGS84Waypoint(lat=53.0, lon=8.0)]),
                _wgs84_stage(),
            ],
        )
    )

    assert updated.stages[0].stage_id == kept
    assert updated.stages[0].waypoints[0].lat == 53.0
    assert updated.stages[1].stage_id != kept


@pytest.mark.asyncio
async def test_an_edit_refuses_an_id_that_is_not_this_missions():
    svc, _, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    other = await svc.create(CreateMissionCommand(name="m2", stages=[_wgs84_stage()]))
    with pytest.raises(StageNotInMission):
        await svc.update(
            UpdateMissionCommand(
                mission_id=created.mission_id,
                stages=[
                    NavigationStageInput(
                        stage_id=other.stages[0].stage_id,
                        waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
                    )
                ],
            )
        )


@pytest.mark.asyncio
async def test_an_edit_refuses_the_same_id_twice():
    svc, _, _, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    kept = created.stages[0].stage_id
    with pytest.raises(DuplicateStageId):
        await svc.update(
            UpdateMissionCommand(
                mission_id=created.mission_id,
                stages=[
                    NavigationStageInput(
                        stage_id=kept, waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)]
                    ),
                    NavigationStageInput(
                        stage_id=kept, waypoints=[WGS84Waypoint(lat=52.4, lon=8.05)]
                    ),
                ],
            )
        )


@pytest.mark.asyncio
async def test_a_stage_spanning_two_sites_is_refused():
    svc, _, _, _, _, _, _ = _make_svc()
    with pytest.raises(StageSpansSites) as raised:
        await svc.create(
            CreateMissionCommand(
                name="m1",
                stages=[
                    NavigationStageInput(
                        waypoints=[
                            SiteLocalWaypoint(site_id=SITE_ID, x=1.0, y=0.5),
                            SiteLocalWaypoint(site_id=SITE_B, x=2.0, y=0.5),
                        ]
                    )
                ],
            )
        )
    assert raised.value.stage_index == 0


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
async def test_update_is_allowed_while_a_run_is_active():
    """The run carries its own copy of the plan, so editing the definition cannot touch it."""
    svc, repo, runs, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    run = await _run_of(runs, created, RunStatus.RUNNING)

    renamed = await svc.update(UpdateMissionCommand(mission_id=created.mission_id, name="renamed"))

    assert renamed.name == "renamed"
    assert (await runs.get(run.run_id)).stages == created.stages


@pytest.mark.asyncio
async def test_update_refuses_an_archived_mission():
    svc, repo, runs, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await _run_of(runs, created, RunStatus.SUCCEEDED)
    await svc.delete(DeleteMissionCommand(mission_id=created.mission_id))
    with pytest.raises(MissionArchived):
        await svc.update(UpdateMissionCommand(mission_id=created.mission_id, name="renamed"))


# delete / archive / restore


@pytest.mark.asyncio
async def test_delete_removes_a_mission_that_never_ran():
    svc, repo, _, _, _, audit_calls, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await svc.delete(DeleteMissionCommand(mission_id=created.mission_id))
    assert await repo.get(created.mission_id) is None
    assert audit_calls[-1]["action"] == "mission.delete"


@pytest.mark.asyncio
async def test_delete_archives_a_mission_that_ran():
    svc, repo, runs, _, _, audit_calls, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    run = await _run_of(runs, created, RunStatus.SUCCEEDED)

    await svc.delete(DeleteMissionCommand(mission_id=created.mission_id))

    archived = await repo.get(created.mission_id)
    assert archived is not None and archived.archived_at is not None
    assert await repo.list() == []
    assert [m.mission_id for m in await repo.list(include_archived=True)] == [created.mission_id]
    assert (await runs.get(run.run_id)).status is RunStatus.SUCCEEDED
    assert audit_calls[-1]["action"] == "mission.archive"


@pytest.mark.asyncio
async def test_delete_refuses_a_mission_with_an_active_run():
    svc, repo, runs, _, _, _, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await _run_of(runs, created, RunStatus.RUNNING)
    with pytest.raises(MissionRunInProgress):
        await svc.delete(DeleteMissionCommand(mission_id=created.mission_id))
    assert (await repo.get(created.mission_id)).archived_at is None


@pytest.mark.asyncio
async def test_restore_brings_an_archived_mission_back():
    svc, repo, runs, _, _, audit_calls, _ = _make_svc()
    created = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    await _run_of(runs, created, RunStatus.SUCCEEDED)
    await svc.delete(DeleteMissionCommand(mission_id=created.mission_id))

    restored = await svc.restore(RestoreMissionCommand(mission_id=created.mission_id))

    assert restored.archived_at is None
    assert audit_calls[-1]["action"] == "mission.restore"
    with pytest.raises(MissionNotArchived):
        await svc.restore(RestoreMissionCommand(mission_id=created.mission_id))


# assign


@pytest.mark.asyncio
async def test_assign_sets_the_default_robot_and_audits():
    svc, repo, _, factsheets, _, audit_calls, _ = _make_svc()
    factsheets.set(_factsheet())
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.assigned_robot_id == ROBOT_ID
    assert audit_calls[-1]["action"] == "mission.assign"
    cleared = await svc.unassign(UnassignMissionCommand(mission_id=mission.mission_id))
    assert cleared.assigned_robot_id is None


@pytest.mark.asyncio
async def test_assign_warns_early_when_the_robot_is_busy():
    svc, repo, runs, factsheets, _, _, _ = _make_svc()
    factsheets.set(_factsheet())
    first = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    second = await svc.create(CreateMissionCommand(name="m2", stages=[_wgs84_stage()]))
    await _run_of(runs, first, RunStatus.RUNNING)
    with pytest.raises(RobotBusy):
        await svc.assign(AssignMissionCommand(mission_id=second.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_assign_rejects_when_factsheet_missing():
    svc, _, _, _, _, _, _ = _make_svc()
    mission = await svc.create(CreateMissionCommand(name="m1", stages=[_wgs84_stage()]))
    with pytest.raises(RobotFactsheetMissing):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_coverage_is_refused_to_a_robot_that_only_drives_points():
    """Declaring navigation is not declaring coverage.

    A robot that visits points would drive a swath as two goals and take any convenient route
    between them, leaving the ground beside the line unworked. That is invisible in telemetry, so
    it has to be refused before dispatch rather than noticed after.
    """
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    mission = await _coverage_mission(svc)
    factsheets.set(_factsheet(frames=[WaypointKind.WGS84]))

    with pytest.raises(UnsupportedStageKind):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_coverage_is_accepted_by_a_robot_that_declares_it():
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    # Declares coverage and turns tightly enough for the path: this is the accepting case.
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

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
async def test_a_mission_with_no_coverage_stage_is_not_kinematically_gated():
    """Only a planned path states a radius it was laid out for; a typed one asserts nothing."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(9.0))
    mission = await svc.create(CreateMissionCommand(name="typed", stages=[_wgs84_stage()]))

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
    field_id = (await repo.get(mission.mission_id)).stages[0].provenance.field_id
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
async def test_assign_refuses_a_plan_whose_field_has_been_deleted():
    """A field carries no history, so a deleted one leaves the plan describing nothing at all."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    await fields.delete((await repo.get(mission.mission_id)).stages[0].provenance.field_id)

    with pytest.raises(StaleCoverageBoundary):
        await svc.assign(AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID))


@pytest.mark.asyncio
async def test_a_mission_without_a_planned_path_never_reads_the_field_catalog():
    """Only a planned path names a field, so a typed mission looks none up."""
    svc, repo, _, factsheets, _, _, _ = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await svc.create(CreateMissionCommand(name="typed", stages=[_wgs84_stage()]))

    class _Explodes:
        async def get(self, field_id):
            raise AssertionError("the field catalog was read for a mission with no plan")

    svc._fields = _Explodes()

    assigned = await svc.assign(
        AssignMissionCommand(mission_id=mission.mission_id, robot_id=ROBOT_ID)
    )

    assert assigned.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_a_planned_mission_can_gain_a_stage_while_its_planned_one_is_carried_along():
    """A planned path is carried by its id through an edit, never rewritten by it."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    planned = mission.stages[0]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[
                CoverageStageInput(stage_id=planned.stage_id),
                NavigationStageInput(waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)]),
            ],
        )
    )

    assert [stage.kind for stage in updated.stages] == ["coverage", "navigation"]
    assert updated.stages[0] == planned


@pytest.mark.asyncio
async def test_dropping_a_planned_stage_from_an_edit_removes_it():
    """A stage the caller does not name is gone, which is how an edit removes one."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[NavigationStageInput(waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)])],
        )
    )

    assert [stage.kind for stage in updated.stages] == ["navigation"]


@pytest.mark.asyncio
async def test_naming_a_coverage_stage_of_another_mission_is_refused():
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(0.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)

    with pytest.raises(StageNotInMission):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[CoverageStageInput(stage_id=uuid4())],
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
    """A planned path is driven, so its waypoints face the same checks as an authored one.

    The swaths name a site that exists, so only reading the whole path reveals the one that
    does not.
    """
    svc, repo, _, _, _, _, _ = _make_svc()
    good = [
        SiteLocalWaypoint(site_id=SITE_ID, x=0.0, y=0.0),
        SiteLocalWaypoint(site_id=SITE_ID, x=1.0, y=0.0),
    ]

    with pytest.raises(UnknownSite):
        await svc.create_generated(
            CreateGeneratedMissionCommand(
                name="smuggled",
                description=None,
                stages=[
                    CoverageStage(
                        stage_id=uuid4(),
                        segments=[
                            Segment(kind="swath", waypoints=good),
                            Segment(
                                kind="turn",
                                waypoints=[
                                    SiteLocalWaypoint(site_id=uuid4(), x=1.0, y=2.0),
                                    SiteLocalWaypoint(site_id=uuid4(), x=3.0, y=4.0),
                                ],
                            ),
                        ],
                        provenance=coverage_provenance(),
                    )
                ],
            )
        )


# --- coverage stages authored through create and update ---------------------------------------


def _coverage_input(field_id: UUID, **overrides) -> CoverageStageInput:
    values = dict(
        field_id=field_id, operation_width_m=3.0, params_robot_id=ROBOT_ID, allow_overlap=False
    )
    values.update(overrides)
    return CoverageStageInput(**values)


async def _authored_coverage(svc, fields, factsheets, **overrides):
    """A mission created from a navigation input and a coverage input, planned by the fake."""
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)
    mission = await svc.create(
        CreateMissionCommand(
            name="both",
            stages=[_wgs84_stage(), _coverage_input(field.id, **overrides)],
        )
    )
    return mission, field


@pytest.mark.asyncio
async def test_create_plans_a_coverage_input_beside_a_navigation_stage():
    svc, _, _, factsheets, _, audit_calls, fields = _make_svc()

    mission, field = await _authored_coverage(svc, fields, factsheets)

    assert [stage.kind for stage in mission.stages] == ["navigation", "coverage"]
    planned = mission.stages[1]
    assert isinstance(planned, CoverageStage)
    assert planned.provenance.field_id == field.id
    assert planned.provenance.planned_for_robot_id == ROBOT_ID
    assert planned.provenance.params.turning_radius_m == 1.5
    assert planned.provenance.param_sources == {
        "turning_radius_m": f"factsheet:{ROBOT_ID}",
        "track_width_m": f"factsheet:{ROBOT_ID}",
        "headland_width_m": "turning_radius",
        "swath_angle_deg": "planner",
    }
    assert len(svc.fake_planner.calls) == 1
    audited = [call for call in audit_calls if call["action"] == "mission.plan_stage"]
    assert len(audited) == 1
    assert audited[0]["payload"]["stage_ids"] == [str(planned.stage_id)]
    assert audited[0]["payload"]["old_digest"] is None
    assert audited[0]["payload"]["new_digest"] == stages_digest(mission.stages)


@pytest.mark.asyncio
async def test_saving_identical_inputs_again_plans_nothing():
    """The editor sends the inputs it loaded; a save that changed nothing must cost nothing."""
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage = mission.stages[1]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[
                NavigationStageInput(
                    stage_id=mission.stages[0].stage_id,
                    waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
                ),
                _coverage_input(field.id, stage_id=stage.stage_id),
            ],
        )
    )

    assert updated.stages[1] == stage
    assert len(svc.fake_planner.calls) == 1


@pytest.mark.asyncio
async def test_a_direction_the_planner_chose_is_kept_until_the_operator_sets_one():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage = mission.stages[1]
    assert stage.provenance.params.swath_angle_deg == 42.5

    kept = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[_coverage_input(field.id, stage_id=stage.stage_id)],
        )
    )
    assert kept.stages[0] == stage
    assert len(svc.fake_planner.calls) == 1

    replanned = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[_coverage_input(field.id, stage_id=stage.stage_id, swath_angle_deg=42.5)],
        )
    )
    assert replanned.stages[0].provenance.param_sources["swath_angle_deg"] == "manual"
    assert len(svc.fake_planner.calls) == 2


@pytest.mark.asyncio
async def test_a_changed_width_replans_under_the_same_stage_id_and_is_audited():
    svc, _, _, factsheets, _, audit_calls, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage = mission.stages[1]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[_coverage_input(field.id, stage_id=stage.stage_id, operation_width_m=4.0)],
        )
    )

    replanned = updated.stages[0]
    assert replanned.stage_id == stage.stage_id
    assert replanned.provenance.params.operation_width_m == 4.0
    assert len(svc.fake_planner.calls) == 2
    audited = [call for call in audit_calls if call["action"] == "mission.plan_stage"]
    assert audited[-1]["payload"]["stage_ids"] == [str(stage.stage_id)]
    assert audited[-1]["payload"]["old_digest"] == stages_digest(mission.stages)
    assert audited[-1]["payload"]["new_digest"] == stages_digest(updated.stages)


@pytest.mark.asyncio
async def test_a_carried_coverage_stage_is_untouched():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, _ = await _authored_coverage(svc, fields, factsheets)
    stage = mission.stages[1]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[CoverageStageInput(stage_id=stage.stage_id)],
        )
    )

    assert updated.stages == [stage]
    assert len(svc.fake_planner.calls) == 1


@pytest.mark.asyncio
async def test_a_coverage_input_naming_a_stage_of_another_mission_is_refused():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)

    with pytest.raises(StageNotInMission):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[_coverage_input(field.id, stage_id=uuid4())],
            )
        )


@pytest.mark.asyncio
async def test_a_planner_failure_leaves_the_mission_untouched():
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    svc.fake_planner.error = CoveragePlannerUnavailable("planner down")

    with pytest.raises(CoveragePlannerUnavailable):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[
                    _coverage_input(
                        field.id, stage_id=mission.stages[1].stage_id, operation_width_m=5.0
                    )
                ],
            )
        )

    assert (await repo.get(mission.mission_id)).stages == mission.stages


@pytest.mark.asyncio
async def test_two_coverage_inputs_on_one_mission_are_both_planned():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)

    mission = await svc.create(
        CreateMissionCommand(
            name="twice",
            stages=[_coverage_input(field.id), _coverage_input(field.id, operation_width_m=2.0)],
        )
    )

    assert [stage.kind for stage in mission.stages] == ["coverage", "coverage"]
    assert mission.stages[0].stage_id != mission.stages[1].stage_id
    assert len(svc.fake_planner.calls) == 2


@pytest.mark.asyncio
async def test_a_coverage_input_keeps_its_cleanup_child():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)

    mission = await svc.create(
        CreateMissionCommand(
            name="with cleanup",
            stages=[_coverage_input(field.id, on_cancel=[_wgs84_stage()])],
        )
    )

    stage = mission.stages[0]
    assert isinstance(stage, CoverageStage)
    assert stage.on_cancel is not None and stage.on_cancel[0].kind == "navigation"


@pytest.mark.asyncio
async def test_a_stage_planned_before_sources_were_recorded_is_replanned_when_edited():
    """Only a carried stage is kept as it is; inputs given in full re-plan an old stage once."""
    svc, repo, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    mission = await _seeded_coverage(svc, repo, fields, radius_planned_for=1.5)
    old = mission.stages[0]
    assert old.provenance.param_sources is None

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[_coverage_input(old.provenance.field_id, stage_id=old.stage_id)],
        )
    )

    assert updated.stages[0].stage_id == old.stage_id
    assert updated.stages[0].provenance.param_sources is not None
    assert len(svc.fake_planner.calls) == 1


@pytest.mark.asyncio
async def test_a_carried_coverage_stage_keeps_its_cleanup_children():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)
    mission = await svc.create(
        CreateMissionCommand(
            name="with cleanup",
            stages=[_coverage_input(field.id, on_cancel=[_wgs84_stage()])],
        )
    )
    stage = mission.stages[0]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[CoverageStageInput(stage_id=stage.stage_id)],
        )
    )

    assert updated.stages == [stage]
    assert updated.stages[0].on_cancel == stage.on_cancel


@pytest.mark.asyncio
async def test_a_stage_id_named_twice_is_refused_before_any_planning():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage_id = mission.stages[1].stage_id
    svc.fake_planner.calls.clear()

    with pytest.raises(DuplicateStageId):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[
                    _coverage_input(field.id, stage_id=stage_id, operation_width_m=4.0),
                    _coverage_input(field.id, stage_id=stage_id, operation_width_m=5.0),
                ],
            )
        )

    assert svc.fake_planner.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"operation_width_m": 3.0},
        {"params_robot_id": "scout"},
        {"headland_width_m": 1.0},
        {"swath_angle_deg": 10.0},
        {"allow_overlap": True},
    ],
)
def test_a_carried_coverage_stage_refuses_every_planning_input(extra):
    with pytest.raises(ValueError):
        CoverageStageInput(stage_id=uuid4(), **extra)


@pytest.mark.asyncio
async def test_a_carried_stage_named_twice_is_refused_before_any_planning():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage_id = mission.stages[1].stage_id
    svc.fake_planner.calls.clear()

    with pytest.raises(DuplicateStageId):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[
                    CoverageStageInput(stage_id=stage_id),
                    _coverage_input(field.id, stage_id=stage_id, operation_width_m=5.0),
                ],
            )
        )

    assert svc.fake_planner.calls == []


@pytest.mark.asyncio
async def test_a_carried_stages_cleanup_child_cannot_be_named_again_elsewhere():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)
    mission = await svc.create(
        CreateMissionCommand(
            name="with cleanup",
            stages=[_coverage_input(field.id, on_cancel=[_wgs84_stage()])],
        )
    )
    stage = mission.stages[0]
    child_id = stage.on_cancel[0].stage_id

    with pytest.raises(DuplicateStageId):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[
                    CoverageStageInput(stage_id=stage.stage_id),
                    NavigationStageInput(
                        stage_id=child_id, waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)]
                    ),
                ],
            )
        )


@pytest.mark.asyncio
async def test_an_empty_cleanup_list_on_a_carried_stage_clears_its_children():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    factsheets.set(_coverage_factsheet(1.5))
    field = seeded_field(fields)
    mission = await svc.create(
        CreateMissionCommand(
            name="with cleanup",
            stages=[_coverage_input(field.id, on_cancel=[_wgs84_stage()])],
        )
    )
    stage = mission.stages[0]

    updated = await svc.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[CoverageStageInput(stage_id=stage.stage_id, on_cancel=[])],
        )
    )

    assert updated.stages[0].on_cancel is None
    assert len(svc.fake_planner.calls) == 1


@pytest.mark.asyncio
async def test_a_navigation_input_reusing_a_coverage_id_is_refused_before_planning():
    svc, _, _, factsheets, _, _, fields = _make_svc()
    mission, field = await _authored_coverage(svc, fields, factsheets)
    stage_id = mission.stages[1].stage_id
    svc.fake_planner.calls.clear()

    with pytest.raises(DuplicateStageId):
        await svc.update(
            UpdateMissionCommand(
                mission_id=mission.mission_id,
                stages=[
                    NavigationStageInput(
                        stage_id=stage_id, waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)]
                    ),
                    _coverage_input(field.id, stage_id=stage_id, operation_width_m=5.0),
                ],
            )
        )

    assert svc.fake_planner.calls == []
