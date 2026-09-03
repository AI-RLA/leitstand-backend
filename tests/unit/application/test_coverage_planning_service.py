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
    MissionNotFoundError,
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
from leitstand_backend.domain.model.mission.mission import MissionStatus
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.ports.inbound.coverage_planning import PlanCoverageCommand
from tests.fakes.fake_coverage_planner import FakeCoveragePlanner
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
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

    async def audit(action, target_type, target_id, payload):
        return None

    mission_uc = MissionManagementService(
        repo=missions,
        dispatcher=FakeMissionDispatcher(),
        factsheets=factsheets,
        events=InMemoryEventPublisher(),
        audit=audit,
        sites=InMemorySiteRepository(),
        fields=fields,
    )
    planner = FakeCoveragePlanner(plan if plan is not None else _plan())
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

    record = await missions.get_record(mission.mission_id)
    assert record is not None
    assert record.status is MissionStatus.DRAFT
    assert record.robot_id is None
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

    record = await missions.get_record(mission.mission_id)
    assert record is not None
    assert record.coverage is not None
    assert record.coverage.field_id == field.id
    assert record.coverage.planned_for_robot_id == ROBOT_ID
    # The recorded params carry the angle the planner resolved, not the absent one it was asked
    # with, or a plan could never be reproduced against the layout it actually used.
    assert record.coverage.params == _params(swath_angle_deg=PLANNED_ANGLE_DEG)
    assert record.coverage.field_area_m2 == FIELD_AREA_M2
    assert record.coverage.metrics.covered_area_m2 == COVERED_AREA_M2
    assert record.coverage.planner_version == "fake 1"


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
    record = await missions.get_record(mission.mission_id)
    assert record is not None and record.coverage is not None


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
async def test_a_replacing_plan_supersedes_the_one_it_names():
    """Re-planning is how a generated plan is changed, so it must replace rather than accumulate."""
    svc, field, _, missions, _ = _make_svc()
    first = await svc.plan(_command(field))

    second = await svc.plan(_command(field).model_copy(update={"replaces": first.mission_id}))

    assert second.mission_id != first.mission_id
    assert await missions.get_record(first.mission_id) is None
    assert await missions.get_record(second.mission_id) is not None


@pytest.mark.asyncio
async def test_a_plan_of_a_different_field_is_not_superseded():
    svc, field, _, missions, _ = _make_svc()
    other = await svc.plan(_command(field))
    # The stored plan claims a field this request is not covering.
    await missions.save_coverage_provenance(
        other.mission_id,
        (await missions.get_record(other.mission_id)).coverage.model_copy(
            update={"field_id": uuid4()}
        ),
    )

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field).model_copy(update={"replaces": other.mission_id}))

    assert await missions.get_record(other.mission_id) is not None


@pytest.mark.asyncio
async def test_a_mission_that_was_not_planned_is_not_superseded():
    """A hand-authored mission is not a re-derivable plan, so replacing it would lose real work."""
    svc, field, _, missions, _ = _make_svc()
    typed = await svc.plan(_command(field))
    missions._coverage.pop(typed.mission_id)

    with pytest.raises(CoveragePlanRejected):
        await svc.plan(_command(field).model_copy(update={"replaces": typed.mission_id}))

    assert await missions.get_record(typed.mission_id) is not None


@pytest.mark.asyncio
async def test_replacing_a_mission_that_does_not_exist_is_refused():
    svc, field, _, _, _ = _make_svc()

    with pytest.raises(MissionNotFoundError):
        await svc.plan(_command(field).model_copy(update={"replaces": uuid4()}))
