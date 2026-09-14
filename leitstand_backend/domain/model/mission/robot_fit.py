"""Whether a robot can drive a mission, judged from its factsheet."""

from __future__ import annotations

from leitstand_backend.domain.errors import (
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.mission import (
    Mission,
    Stage,
    StageKind,
    stage_waypoints,
)
from leitstand_backend.domain.model.mission.stage_rules import coverage_stages
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet, WaypointKind


def validate_against_factsheet(
    mission: Mission,
    robot_id: str,
    factsheet: RobotFactsheet,
) -> None:
    """Reject missions whose stage kinds or waypoint frames the robot has not declared.

    Only the stage kind and the waypoint frame are gated; which specific sites the robot has a map
    for is not.
    """
    for stage in mission.stages:
        try:
            kind = StageKind(stage.kind)
        except ValueError:
            raise UnsupportedStageKind(robot_id, stage.stage_id, stage.kind)

        # Coverage is declared apart from navigation because driving a swath as a line is not
        # the same capability as visiting points.
        if kind is StageKind.NAVIGATION:
            capability = factsheet.navigation
        elif kind is StageKind.COVERAGE:
            capability = factsheet.coverage
        else:
            capability = None
        if capability is None:
            raise UnsupportedStageKind(robot_id, stage.stage_id, stage.kind)

        supported_frames = set(capability.supported_waypoint_kinds)
        for waypoint in stage_waypoints(stage):
            frame = WaypointKind(waypoint.kind)
            if frame not in supported_frames:
                raise UnsupportedWaypointFrame(robot_id, stage.stage_id, frame.value)


def validate_plan_fits_robot(robot_id: str, stages: list[Stage], factsheet: RobotFactsheet) -> None:
    """Reject a planned path this machine cannot drive as it was laid out.

    A wider-turning machine cuts every corner silently, and one wider than the implement the
    swaths were spaced for drives over the strip it just worked. Each coverage stage is
    checked on its own parameters, which two stages of one mission need not share.
    """
    planned = coverage_stages(stages)
    if not planned:
        return
    if factsheet.physical_parameters is None:
        raise RobotPhysicalParametersMissing(robot_id)
    robot_radius_m = factsheet.physical_parameters.min_turning_radius_m
    track_width_m = factsheet.physical_parameters.track_width_m
    for stage in planned:
        params = stage.provenance.params
        if robot_radius_m > params.turning_radius_m:
            raise IncompatibleTurningRadius(robot_id, robot_radius_m, params.turning_radius_m)
        if track_width_m > params.operation_width_m:
            raise ImplementNarrowerThanRobot(robot_id, track_width_m, params.operation_width_m)
