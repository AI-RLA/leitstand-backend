"""Named-field mapper from the proto Factsheet to the in-house RobotFactsheet.

The anti-corruption layer for the factsheet direction. Identity (``robot_id``)
is not carried on the wire -- it is stamped from the transport key here.
"""

from __future__ import annotations

from leitstand.robot.v1 import factsheet_pb2

from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)

_WAYPOINT_KIND_FROM_PROTO: dict[int, WaypointKind] = {
    factsheet_pb2.WAYPOINT_KIND_WGS84: WaypointKind.WGS84,
    factsheet_pb2.WAYPOINT_KIND_SITE_LOCAL: WaypointKind.SITE_LOCAL,
}


def _frames_from_proto(kinds) -> list[WaypointKind]:
    frames: list[WaypointKind] = []
    for kind in kinds:
        frame = _WAYPOINT_KIND_FROM_PROTO.get(kind)
        if frame is None:
            raise ValueError(f"unmapped waypoint kind {kind} in factsheet")
        frames.append(frame)
    return frames


def factsheet_from_proto(proto: factsheet_pb2.Factsheet, robot_id: str) -> RobotFactsheet:
    """Rebuild the in-house factsheet; ``robot_id`` comes from the transport key."""
    navigation: NavigationCapability | None = None
    coverage: CoverageCapability | None = None
    for cap in proto.stage_capabilities:
        detail = cap.WhichOneof("detail")
        if detail == "navigation":
            if navigation is not None:
                raise ValueError("factsheet declares the NAVIGATION capability more than once")
            navigation = NavigationCapability(
                supported_waypoint_kinds=_frames_from_proto(cap.navigation.supported_waypoint_kinds)
            )
        elif detail == "coverage":
            if coverage is not None:
                raise ValueError("factsheet declares the COVERAGE capability more than once")
            coverage = CoverageCapability(
                supported_waypoint_kinds=_frames_from_proto(cap.coverage.supported_waypoint_kinds)
            )
        # Anything else is an empty entry, or a future kind the backend does not gate on.
    return RobotFactsheet(
        robot_id=robot_id,
        navigation=navigation,
        coverage=coverage,
        physical_parameters=_physical_parameters_from_proto(proto),
    )


def _physical_parameters_from_proto(
    proto: factsheet_pb2.Factsheet,
) -> PhysicalParameters | None:
    """Map the declared physical parameters, or None where the robot declared none.

    Presence is read from the message rather than from its values, because zero is a meaningful
    turning radius (a robot that turns on the spot) and would otherwise be indistinguishable from
    an undeclared one.
    """
    if not proto.HasField("physical_parameters"):
        return None
    return PhysicalParameters(
        track_width_m=proto.physical_parameters.track_width_m,
        min_turning_radius_m=proto.physical_parameters.min_turning_radius_m,
    )
