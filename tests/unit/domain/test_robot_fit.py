"""Whether a robot can drive a mission, checked on the domain functions directly."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from leitstand_backend.domain.errors import (
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.robot_fit import (
    validate_against_factsheet,
    validate_plan_fits_robot,
)
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from tests.fakes.planned_coverage import coverage_stage

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def _mission(*stages) -> Mission:
    return Mission(
        mission_id=uuid4(), name="m", stages=list(stages), created_at=NOW, updated_at=NOW
    )


def _factsheet(**kwargs) -> RobotFactsheet:
    return RobotFactsheet(robot_id="r1", **kwargs)


def test_a_stage_kind_the_robot_did_not_declare_is_refused() -> None:
    mission = _mission(coverage_stage())
    with pytest.raises(UnsupportedStageKind):
        validate_against_factsheet(mission, "r1", _factsheet(navigation=NavigationCapability()))


def test_a_waypoint_frame_the_robot_did_not_declare_is_refused() -> None:
    stage = NavigationStage(
        stage_id=uuid4(), waypoints=[SiteLocalWaypoint(site_id=uuid4(), x=0, y=0)]
    )
    sheet = _factsheet(
        navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84])
    )
    with pytest.raises(UnsupportedWaypointFrame):
        validate_against_factsheet(_mission(stage), "r1", sheet)


def test_a_declared_kind_and_frame_pass() -> None:
    stage = NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])
    sheet = _factsheet(
        navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
        coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
    )
    validate_against_factsheet(_mission(stage, coverage_stage()), "r1", sheet)


def test_a_plan_needs_the_robots_physical_parameters() -> None:
    with pytest.raises(RobotPhysicalParametersMissing):
        validate_plan_fits_robot("r1", [coverage_stage()], _factsheet())


def test_a_wider_turning_robot_is_refused() -> None:
    sheet = _factsheet(
        physical_parameters=PhysicalParameters(track_width_m=1.0, min_turning_radius_m=2.0)
    )
    with pytest.raises(IncompatibleTurningRadius):
        validate_plan_fits_robot("r1", [coverage_stage(turning_radius_m=1.5)], sheet)


def test_a_robot_wider_than_the_implement_is_refused() -> None:
    sheet = _factsheet(
        physical_parameters=PhysicalParameters(track_width_m=4.0, min_turning_radius_m=0.0)
    )
    with pytest.raises(ImplementNarrowerThanRobot):
        validate_plan_fits_robot("r1", [coverage_stage(operation_width_m=3.0)], sheet)


def test_a_mission_without_coverage_needs_no_parameters() -> None:
    stage = NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])
    validate_plan_fits_robot("r1", [stage], _factsheet())
