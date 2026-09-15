"""The coverage stage planner function: where the machine values come from, and what counts as a change."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leitstand_backend.application.coverage_stage_planner import (
    CoverageInputs,
    plan_coverage_stage,
    unchanged,
)
from leitstand_backend.domain.errors import (
    FieldNotFoundError,
    RobotFactsheetMissing,
    TurningRadiusBelowRobot,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.field import Field
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from tests.fakes.coverage_stage_planning import (
    LINEAR_CURV_CHANGE,
    PLANNED_ANGLE_DEG,
    TURN_SAMPLE_M,
    canned_plan,
)
from tests.fakes.fake_coverage_planner import FakeCoveragePlanner
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.planned_coverage import PLANNED_AT, seeded_field

ROBOT_ID = "bonirob2"
ROBOT_RADIUS_M = 1.5
ROBOT_TRACK_M = 1.2


def _factsheet(coverage: bool = True) -> RobotFactsheet:
    return RobotFactsheet(
        robot_id=ROBOT_ID,
        navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84])
        if coverage
        else None,
        physical_parameters=PhysicalParameters(
            track_width_m=ROBOT_TRACK_M, min_turning_radius_m=ROBOT_RADIUS_M
        ),
    )


class _World:
    def __init__(self) -> None:
        self.fields = InMemoryFieldRepository()
        self.factsheets = InMemoryRobotFactsheetView()
        self.factsheets.set(_factsheet())
        self.field = seeded_field(self.fields)
        self.planner = FakeCoveragePlanner(canned_plan())

    def inputs(self, **overrides) -> CoverageInputs:
        values = dict(
            field_id=self.field.id,
            operation_width_m=3.0,
            params_robot_id=ROBOT_ID,
            turning_radius_m=None,
            headland_width_m=None,
            swath_angle_deg=None,
            allow_overlap=False,
        )
        values.update(overrides)
        return CoverageInputs(**values)

    async def plan(self, inputs: CoverageInputs):
        return await plan_coverage_stage(
            inputs,
            fields=self.fields,
            factsheets=self.factsheets,
            planner=self.planner,
            turn_sample_m=TURN_SAMPLE_M,
            linear_curv_change=LINEAR_CURV_CHANGE,
        )

    async def unchanged(self, inputs: CoverageInputs, stored) -> bool:
        return await unchanged(inputs, stored, fields=self.fields, factsheets=self.factsheets)


@pytest.mark.asyncio
async def test_the_factsheet_fills_the_radius_and_the_track_width():
    world = _World()

    stage = await world.plan(world.inputs())

    params = stage.provenance.params
    assert params.turning_radius_m == ROBOT_RADIUS_M
    assert params.track_width_m == ROBOT_TRACK_M
    assert params.headland_width_m == ROBOT_RADIUS_M
    assert params.turn_sample_m == TURN_SAMPLE_M
    assert params.linear_curv_change == LINEAR_CURV_CHANGE
    assert stage.provenance.planned_for_robot_id == ROBOT_ID
    assert stage.provenance.param_sources == {
        "turning_radius_m": f"factsheet:{ROBOT_ID}",
        "track_width_m": f"factsheet:{ROBOT_ID}",
        "headland_width_m": "turning_radius",
        "swath_angle_deg": "planner",
    }


@pytest.mark.asyncio
async def test_a_wider_radius_than_the_robots_is_accepted_and_recorded_as_manual():
    world = _World()

    stage = await world.plan(world.inputs(turning_radius_m=2.5))

    assert stage.provenance.params.turning_radius_m == 2.5
    assert stage.provenance.param_sources["turning_radius_m"] == "manual"
    assert stage.provenance.param_sources["track_width_m"] == f"factsheet:{ROBOT_ID}"


@pytest.mark.asyncio
async def test_a_tighter_radius_than_the_robots_is_refused():
    world = _World()

    with pytest.raises(TurningRadiusBelowRobot) as excinfo:
        await world.plan(world.inputs(turning_radius_m=1.0))

    assert excinfo.value.min_m == ROBOT_RADIUS_M
    assert world.planner.calls == []


@pytest.mark.asyncio
async def test_without_a_robot_the_radius_is_the_operators_and_no_track_width_is_known():
    world = _World()

    stage = await world.plan(world.inputs(params_robot_id=None, turning_radius_m=0.8))

    assert stage.provenance.planned_for_robot_id is None
    assert stage.provenance.params.turning_radius_m == 0.8
    assert stage.provenance.params.track_width_m is None
    assert stage.provenance.param_sources == {
        "turning_radius_m": "manual",
        "headland_width_m": "turning_radius",
        "swath_angle_deg": "planner",
    }


@pytest.mark.asyncio
async def test_a_given_headland_and_direction_are_kept_and_recorded_as_manual():
    world = _World()

    stage = await world.plan(world.inputs(headland_width_m=4.0, swath_angle_deg=42.5))

    assert stage.provenance.params.headland_width_m == 4.0
    assert stage.provenance.param_sources["headland_width_m"] == "manual"
    assert stage.provenance.param_sources["swath_angle_deg"] == "manual"


@pytest.mark.asyncio
async def test_the_planners_own_direction_is_what_the_record_carries():
    world = _World()

    stage = await world.plan(world.inputs())

    assert stage.provenance.params.swath_angle_deg == PLANNED_ANGLE_DEG
    assert world.planner.calls[0][1].swath_angle_deg is None


@pytest.mark.asyncio
async def test_an_unknown_field_and_a_robot_without_coverage_are_refused():
    world = _World()

    with pytest.raises(FieldNotFoundError):
        await world.plan(world.inputs(field_id=uuid4()))
    with pytest.raises(RobotFactsheetMissing):
        await world.plan(world.inputs(params_robot_id="nobody"))
    world.factsheets.set(_factsheet(coverage=False))
    with pytest.raises(UnsupportedStageKind):
        await world.plan(world.inputs())


@pytest.mark.asyncio
async def test_identical_inputs_reproduce_the_stored_plan():
    world = _World()
    stored = await world.plan(world.inputs())

    assert await world.unchanged(world.inputs(), stored)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"operation_width_m": 4.0},
        {"turning_radius_m": 2.0},
        {"headland_width_m": 0.5},
        {"allow_overlap": True},
        {"swath_angle_deg": 10.0},
        {"params_robot_id": None, "turning_radius_m": 1.5},
    ],
)
async def test_each_changed_input_is_a_change(change: dict):
    world = _World()
    stored = await world.plan(world.inputs())

    assert not await world.unchanged(world.inputs(**change), stored)


@pytest.mark.asyncio
async def test_the_planners_direction_given_back_as_a_number_is_a_change():
    """A number pins the direction the operator saw; the planner might choose another next time."""
    world = _World()
    stored = await world.plan(world.inputs())

    assert not await world.unchanged(world.inputs(swath_angle_deg=PLANNED_ANGLE_DEG), stored)


@pytest.mark.asyncio
async def test_a_manual_direction_given_again_is_no_change():
    world = _World()
    stored = await world.plan(world.inputs(swath_angle_deg=42.5))

    assert await world.unchanged(world.inputs(swath_angle_deg=42.5), stored)
    assert not await world.unchanged(world.inputs(), stored)


@pytest.mark.asyncio
async def test_an_edited_boundary_is_a_change():
    world = _World()
    stored = await world.plan(world.inputs())
    moved = world.field.geometry.model_copy(
        update={
            "coordinates": [
                [(8.05, 52.3), (8.07, 52.3), (8.07, 52.31), (8.05, 52.31), (8.05, 52.3)]
            ]
        }
    )
    world.fields.seed(
        Field(
            id=world.field.id,
            name=world.field.name,
            geometry=moved,
            area_ha=1.2,
            notes=None,
            created_at=PLANNED_AT,
            updated_at=PLANNED_AT,
        )
    )

    assert not await world.unchanged(world.inputs(), stored)


@pytest.mark.asyncio
async def test_a_robot_whose_radius_changed_makes_its_plans_stale():
    """The factsheet is read again, so a machine re-declared with a wider turn re-plans."""
    world = _World()
    stored = await world.plan(world.inputs())
    world.factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(
                track_width_m=ROBOT_TRACK_M, min_turning_radius_m=2.0
            ),
        )
    )

    assert not await world.unchanged(world.inputs(), stored)


@pytest.mark.asyncio
async def test_a_manual_direction_survives_the_planners_float_round_trip():
    """The stored angle is the planner's echo, which may come back through radians."""
    world = _World()
    world.planner.result = canned_plan().model_copy(update={"swath_angle_deg": 42.50000000000001})
    stored = await world.plan(world.inputs(swath_angle_deg=42.5))

    assert await world.unchanged(world.inputs(swath_angle_deg=42.5), stored)


@pytest.mark.asyncio
async def test_a_changed_track_width_is_a_change():
    world = _World()
    stored = await world.plan(world.inputs())
    world.factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_ID,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(
                track_width_m=1.4, min_turning_radius_m=ROBOT_RADIUS_M
            ),
        )
    )

    assert not await world.unchanged(world.inputs(), stored)
