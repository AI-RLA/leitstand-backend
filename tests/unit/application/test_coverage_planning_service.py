"""CoveragePlanningService: a generated path is checked before it becomes a mission.

The operator approving a coverage mission sees a list of coordinates, and cannot tell a path that
covers the field from one wrong by a factor of ten. These tests pin the checks that can, and pin
that a refused plan leaves nothing behind.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from geojson_pydantic import Polygon

from leitstand_backend.application.coverage_planning_service import CoveragePlanningService
from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.domain.errors import (
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.field import Field
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoverageParams,
    CoveragePlan,
    PlannedSegment,
)
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.ports.inbound.coverage_planning import PlanCoverageCommand
from tests.fakes.coverage_stage_planning import bind_stage_planning
from tests.fakes.fake_coverage_planner import FakeCoveragePlanner
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_site_repository import InMemorySiteRepository

ROBOT_ID = "bonirob2"
UTC_NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)

# One hectare, and a plan over it at a 3 m working width: 2700 m of track sweeps 8100 m2, which is
# the 81% an F2C plan leaves after headland turns on a real field.
FIELD_AREA_HA = 1.0
FIELD_AREA_M2 = 10_000.0
OPERATION_WIDTH_M = 3.0
# The planner resolves the angle when the caller leaves it open, so the fake answers one.
PLANNED_ANGLE_DEG = 42.5
LINEAR_CURV_CHANGE = 200.0
TURN_SAMPLE_M = 0.25
TURNING_RADIUS_M = 1.5
HEADLAND_WIDTH_M = 0.5
TRACK_LENGTH_M = 2700.0
COVERED_AREA_M2 = 8100.0


def _polygon() -> Polygon:
    return Polygon(
        type="Polygon",
        coordinates=[
            [
                [8.0000, 52.0000],
                [8.0015, 52.0000],
                [8.0015, 52.0009],
                [8.0000, 52.0009],
                [8.0000, 52.0000],
            ]
        ],
    )


def _mainland() -> Polygon:
    """The field less a headland, which is what the planner returns alongside the segments."""
    return Polygon(
        type="Polygon",
        coordinates=[
            [
                [8.0001, 52.0001],
                [8.0014, 52.0001],
                [8.0014, 52.0008],
                [8.0001, 52.0008],
                [8.0001, 52.0001],
            ]
        ],
    )


def _field(area_ha: float | None = FIELD_AREA_HA) -> Field:
    return Field(
        id=uuid4(),
        name="Agrotechnicum north",
        geometry=_polygon(),
        area_ha=area_ha,
        notes=None,
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )


def _waypoints(count: int = 6) -> list[WGS84Waypoint]:
    return [WGS84Waypoint(lat=52.0 + i * 0.0001, lon=8.0 + i * 0.0001) for i in range(count)]


def _plan(
    *,
    waypoints: list[WGS84Waypoint] | None = None,
    covered_area_m2: float = COVERED_AREA_M2,
    track_length_m: float = TRACK_LENGTH_M,
    mainland_area_m2: float | None = None,
    swath_count: int | None = None,
    mainland_boundary: Polygon | None = None,
) -> CoveragePlan:
    points = waypoints if waypoints is not None else _waypoints()
    # One swath per pair, which is the shape the planner returns: a swath is its endpoints. Turns
    # are omitted; nothing under test reads them, and a plan of swaths alone is still a valid route.
    segments = [
        PlannedSegment(kind="swath", waypoints=points[i : i + 2])
        for i in range(0, len(points) - 1, 2)
    ]
    return CoveragePlan(
        segments=segments,
        mainland_boundary=mainland_boundary or _mainland(),
        swath_angle_deg=PLANNED_ANGLE_DEG,
        metrics=CoverageMetrics(
            swath_count=len(segments) if swath_count is None else swath_count,
            track_length_m=track_length_m,
            covered_area_m2=covered_area_m2,
            mainland_area_m2=mainland_area_m2,
        ),
        planner_version="fake 1",
    )


def _params(*, swath_angle_deg: float | None = None) -> CoverageParams:
    """What the planner should be handed: the request's width, the robot's own radius."""
    return CoverageParams(
        swath_angle_deg=swath_angle_deg,
        operation_width_m=OPERATION_WIDTH_M,
        turning_radius_m=TURNING_RADIUS_M,
        headland_width_m=HEADLAND_WIDTH_M,
        linear_curv_change=LINEAR_CURV_CHANGE,
        track_width_m=1.5,
        allow_overlap=False,
        turn_sample_m=TURN_SAMPLE_M,
    )


def _mission_waypoints(mission) -> list:
    """Flatten the created stage back to a waypoint list, for order and identity assertions."""
    return [wp for seg in mission.stages[0].segments for wp in seg.waypoints]


def _make_svc(field: Field | None = None, plan: CoveragePlan | None = None):
    fields = InMemoryFieldRepository()
    seeded = field if field is not None else _field()
    fields.seed(seeded)

    factsheets = InMemoryRobotFactsheetView()
    factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(
                track_width_m=1.5, min_turning_radius_m=TURNING_RADIUS_M
            ),
        )
    )

    missions = InMemoryMissionRepository()
    runs = InMemoryMissionRunRepository()

    async def audit(action, target_type, target_id, payload):
        return None

    planner = FakeCoveragePlanner(plan if plan is not None else _plan())
    plan_stage, unchanged, _ = bind_stage_planning(fields, factsheets, planner)
    mission_uc = MissionManagementService(
        repo=missions,
        runs=runs,
        factsheets=factsheets,
        events=InMemoryEventPublisher(),
        audit=audit,
        sites=InMemorySiteRepository(),
        fields=fields,
        planner=plan_stage,
        unchanged=unchanged,
    )
    svc = CoveragePlanningService(
        fields=fields,
        factsheets=factsheets,
        planner=planner,
        missions=mission_uc,
        repo=missions,
        turn_sample_m=TURN_SAMPLE_M,
        linear_curv_change=LINEAR_CURV_CHANGE,
    )
    return svc, seeded, planner, missions, factsheets


def _command(field: Field) -> PlanCoverageCommand:
    return PlanCoverageCommand(
        field_id=field.id,
        robot_id=ROBOT_ID,
        name="cover north",
        description=None,
        operation_width_m=OPERATION_WIDTH_M,
        headland_width_m=HEADLAND_WIDTH_M,
    )


@pytest.mark.asyncio
async def test_a_plan_becomes_a_draft_mission_with_one_coverage_stage():
    svc, field, _, missions, _ = _make_svc()

    mission = await svc.plan(_command(field))

    record = await missions.get(mission.mission_id)
    assert record is not None
    assert record.assigned_robot_id is None
    assert len(mission.stages) == 1
    assert mission.stages[0].kind == "coverage"


@pytest.mark.asyncio
async def test_the_missions_waypoints_are_exactly_the_planners_in_order():
    """The geometry approved must be the geometry planned; nothing may re-derive it."""
    planned = _waypoints(8)
    svc, field, _, _, _ = _make_svc(plan=_plan(waypoints=planned))

    mission = await svc.plan(_command(field))

    assert _mission_waypoints(mission) == planned


@pytest.mark.asyncio
async def test_a_planner_failure_creates_no_mission():
    svc, field, planner, missions, _ = _make_svc()
    planner.error = CoveragePlannerUnavailable()

    with pytest.raises(CoveragePlannerUnavailable):
        await svc.plan(_command(field))

    assert await missions.list() == []


@pytest.mark.asyncio
async def test_a_deep_headland_on_a_small_field_is_not_mistaken_for_a_wrong_plan():
    """A headland is ground the operator excluded, not ground the plan failed to cover.

    Measured against the field this plan covers a quarter of it and reads as an order-of-magnitude
    error; measured against the mainland the headland left it covers nearly all of it.
    """
    svc, field, _, missions, _ = _make_svc(
        plan=_plan(
            covered_area_m2=FIELD_AREA_M2 * 0.235,
            track_length_m=TRACK_LENGTH_M * 0.235,
            mainland_area_m2=FIELD_AREA_M2 * 0.29,
        )
    )

    await svc.plan(_command(field))

    assert len(await missions.list()) == 1


@pytest.mark.asyncio
async def test_a_plan_leaving_most_of_the_mainland_undriven_is_refused():
    """The floor still holds once it is measured against the right ground."""
    svc, field, _, missions, _ = _make_svc(
        plan=_plan(
            covered_area_m2=FIELD_AREA_M2 * 0.05,
            track_length_m=TRACK_LENGTH_M * 0.05,
            mainland_area_m2=FIELD_AREA_M2 * 0.9,
        )
    )

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field))

    assert await missions.list() == []


@pytest.mark.asyncio
async def test_a_plan_covering_far_more_than_the_field_is_refused():
    """The order-of-magnitude guard: a self-consistent plan can still be for the wrong field."""
    svc, field, _, missions, _ = _make_svc(
        plan=_plan(covered_area_m2=COVERED_AREA_M2 * 10, track_length_m=TRACK_LENGTH_M * 10)
    )

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field))

    assert await missions.list() == []


@pytest.mark.asyncio
async def test_a_plan_whose_track_and_area_disagree_is_refused():
    """Track length times working width is the area swept, so the two cannot contradict."""
    svc, field, _, _, _ = _make_svc(plan=_plan(track_length_m=TRACK_LENGTH_M / 10))

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field))


@pytest.mark.asyncio
async def test_a_robot_with_no_factsheet_is_refused():
    svc, field, planner, _, factsheets = _make_svc()
    factsheets.clear(ROBOT_ID)

    with pytest.raises(RobotFactsheetMissing):
        await svc.plan(_command(field))

    assert planner.calls == []


@pytest.mark.asyncio
async def test_the_fields_polygon_reaches_the_planner_unmodified():
    svc, field, planner, _, _ = _make_svc()

    await svc.plan(_command(field))

    (boundary, params) = planner.calls[0]
    assert boundary == field.geometry
    assert params == _params()


@pytest.mark.asyncio
async def test_the_mission_records_what_it_was_planned_from():
    """A plan can only be judged or reproduced against the inputs that produced it."""
    svc, field, _, missions, _ = _make_svc()

    mission = await svc.plan(_command(field))

    record = await missions.get(mission.mission_id)
    assert record is not None
    provenance = record.stages[0].provenance
    assert provenance.field_id == field.id
    assert provenance.planned_for_robot_id == ROBOT_ID
    # The recorded params carry the angle the planner resolved, not the absent one it was asked
    # with, or a plan could never be reproduced against the layout it actually used.
    assert provenance.params == _params(swath_angle_deg=PLANNED_ANGLE_DEG)
    assert provenance.field_area_m2 == FIELD_AREA_M2
    assert provenance.metrics.covered_area_m2 == COVERED_AREA_M2
    assert provenance.planner_version == "fake 1"


@pytest.mark.asyncio
async def test_a_plan_whose_metrics_do_not_match_its_geometry_is_refused():
    """Metrics are recorded and shown, so a count that disagrees with the swaths misinforms twice.

    The operator would read one number on the card and see another drawn on the map, with nothing
    in between to say which is the plan.
    """
    svc, field, _, _, _ = _make_svc(plan=_plan(swath_count=33))

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field))


@pytest.mark.asyncio
async def test_the_sampling_the_path_was_built_at_is_recorded():
    """Point spacing decides what a field costs to dispatch, so a plan must say what it used."""
    svc, field, planner, missions, _ = _make_svc()

    mission = await svc.plan(_command(field))

    (_, params) = planner.calls[0]
    assert params.turn_sample_m == TURN_SAMPLE_M
    record = await missions.get(mission.mission_id)
    assert record is not None and record.stages[0].provenance is not None


@pytest.mark.asyncio
async def test_an_unnamed_mission_takes_the_fields_name():
    """The operator naming a field to cover has already said what the mission is."""
    svc, field, _, _, _ = _make_svc()

    mission = await svc.plan(
        PlanCoverageCommand(
            field_id=field.id, robot_id=ROBOT_ID, operation_width_m=OPERATION_WIDTH_M
        )
    )

    assert mission.name == f"Coverage of {field.name}"


@pytest.mark.asyncio
async def test_a_given_name_is_kept():
    svc, field, _, _, _ = _make_svc()

    mission = await svc.plan(_command(field))

    assert mission.name == "cover north"


@pytest.mark.asyncio
async def test_the_turning_radius_comes_from_the_robots_own_declaration():
    """The caller cannot supply it, so a plan cannot be laid out for a turn the robot cannot make."""
    svc, field, planner, _, _ = _make_svc()

    await svc.plan(_command(field))

    (_, params) = planner.calls[0]
    assert params.turning_radius_m == TURNING_RADIUS_M
    assert params.operation_width_m == OPERATION_WIDTH_M


@pytest.mark.asyncio
async def test_a_robot_that_declared_no_physical_parameters_is_refused():
    """Better a refusal naming the robot than a path planned against a guessed radius."""
    svc, field, planner, _, factsheets = _make_svc()
    factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        )
    )

    with pytest.raises(RobotPhysicalParametersMissing):
        await svc.plan(_command(field))

    assert planner.calls == []


@pytest.mark.asyncio
async def test_a_robot_that_cannot_drive_swaths_is_refused_before_planning():
    """Refused here rather than at assign, so nobody reviews a plan the robot cannot execute."""
    svc, field, planner, _, factsheets = _make_svc()
    factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(
                track_width_m=1.5, min_turning_radius_m=TURNING_RADIUS_M
            ),
        )
    )

    with pytest.raises(UnsupportedStageKind):
        await svc.plan(_command(field))

    assert planner.calls == []


@pytest.mark.asyncio
async def test_a_field_with_no_computed_area_is_refused():
    svc, field, planner, _, _ = _make_svc(field=_field(area_ha=None))

    with pytest.raises(FieldNotPlannable):
        await svc.plan(_command(field))

    assert planner.calls == []


@pytest.mark.asyncio
async def test_an_unknown_field_is_refused():
    svc, _, planner, _, _ = _make_svc()

    with pytest.raises(FieldNotFoundError):
        await svc.plan(
            PlanCoverageCommand(
                field_id=uuid4(),
                robot_id=ROBOT_ID,
                name="cover nowhere",
                operation_width_m=OPERATION_WIDTH_M,
            )
        )

    assert planner.calls == []


@pytest.mark.asyncio
async def test_re_planning_rewrites_the_mission_in_place():
    """Re-planning is how a generated plan is changed; the mission keeps its id and its stage."""
    svc, field, planner, missions, _ = _make_svc()
    first = await svc.plan(_command(field))
    stage_id = first.stages[0].stage_id
    planner.result = _plan(waypoints=_waypoints(8))

    second = await svc.plan(_command(field).model_copy(update={"replan": first.stages[0].stage_id}))

    assert second.mission_id == first.mission_id
    assert second.stages[0].stage_id == stage_id
    assert len(_mission_waypoints(second)) == 8
    assert (await missions.get(first.mission_id)).stages[0].provenance.planned_at >= (
        first.stages[0].provenance.planned_at
    )


@pytest.mark.asyncio
async def test_re_planning_after_a_run_keeps_the_run_intact():
    """The run carries the plan and provenance it executed; overwriting the definition's
    changes nothing it holds."""
    from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
    from leitstand_backend.domain.model.mission.run_status import RunStatus
    from leitstand_backend.domain.model.mission.stages_digest import stages_digest

    svc, field, planner, missions, _ = _make_svc()
    first = await svc.plan(_command(field))
    record = await missions.get(first.mission_id)
    runs = svc._missions._runs  # the fake behind the management service
    run = await runs.create(
        MissionRun(
            run_id=uuid4(),
            mission_id=first.mission_id,
            robot_id=ROBOT_ID,
            status=RunStatus.SUCCEEDED,
            stages=first.stages,
            stages_digest=stages_digest(first.stages),
            origin=RunOrigin(kind="manual", actor="human"),
            created_at=UTC_NOW,
            updated_at=UTC_NOW,
        )
    )
    planner.result = _plan(waypoints=_waypoints(8))

    replanned = await svc.plan(
        _command(field).model_copy(update={"replan": first.stages[0].stage_id})
    )

    again = await runs.get(run.run_id)
    assert again.stages == first.stages
    assert again.stages_digest == stages_digest(first.stages)
    # The run's own copy of how its path was derived is untouched by the re-plan.
    assert again.stages[0].provenance == record.stages[0].provenance
    assert replanned.stages[0].stage_id == first.stages[0].stage_id
    assert stages_digest(replanned.stages) != again.stages_digest


@pytest.mark.asyncio
async def test_a_plan_of_a_different_field_is_not_re_planned():
    svc, field, _, missions, _ = _make_svc()
    other = await svc.plan(_command(field))
    # The stored path claims a field this request is not covering.
    stored = await missions.get(other.mission_id)
    stage = stored.stages[0]
    await missions.save(
        stored.model_copy(
            update={
                "stages": [
                    stage.model_copy(
                        update={
                            "provenance": stage.provenance.model_copy(update={"field_id": uuid4()})
                        }
                    )
                ]
            }
        )
    )

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field).model_copy(update={"replan": stage.stage_id}))


@pytest.mark.asyncio
async def test_a_stage_that_does_not_exist_is_not_re_planned():
    """Only a coverage stage can be superseded, so an unknown id overwrites nothing."""
    svc, field, _, missions, _ = _make_svc()
    await svc.plan(_command(field))

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field).model_copy(update={"replan": uuid4()}))


@pytest.mark.asyncio
async def test_re_planning_a_stage_that_does_not_exist_is_refused():
    svc, field, _, _, _ = _make_svc()

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field).model_copy(update={"replan": uuid4()}))


@pytest.mark.asyncio
async def test_a_coverage_stage_nested_under_on_cancel_is_re_planned():
    """The planner resolves its target through the whole tree, so the rewrite must reach as far."""
    from leitstand_backend.domain.model.mission.mission import NavigationStage

    svc, field, planner, missions, _ = _make_svc()
    first = await svc.plan(_command(field))
    stored = await missions.get(first.mission_id)
    coverage = stored.stages[0]
    await missions.save(
        stored.model_copy(
            update={
                "stages": [
                    NavigationStage(
                        stage_id=uuid4(),
                        waypoints=_waypoints(2),
                        on_cancel=[coverage],
                    )
                ]
            }
        )
    )
    planner.result = _plan(waypoints=_waypoints(8))

    replanned = await svc.plan(_command(field).model_copy(update={"replan": coverage.stage_id}))

    nested = replanned.stages[0].on_cancel[0]
    assert nested.stage_id == coverage.stage_id
    assert nested != coverage
