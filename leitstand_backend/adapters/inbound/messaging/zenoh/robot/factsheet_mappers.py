"""Named-field mapper from the proto Factsheet to the in-house RobotFactsheet.

The anti-corruption layer for the factsheet direction. Identity (``robot_id``)
is not carried on the wire -- it is stamped from the transport key here.
"""

from __future__ import annotations

from leitstand.robot.v1 import factsheet_pb2

from leitstand_backend.domain.model.robot.robot_factsheet import (
    NavigationCapability,
    RobotFactsheet,
    WaypointKind,
)

_WAYPOINT_KIND_FROM_PROTO: dict[int, WaypointKind] = {
    factsheet_pb2.WAYPOINT_KIND_WGS84: WaypointKind.WGS84,
    factsheet_pb2.WAYPOINT_KIND_SITE_LOCAL: WaypointKind.SITE_LOCAL,
}


def _navigation_from_proto(cap: factsheet_pb2.NavigationCapability) -> NavigationCapability:
    frames: list[WaypointKind] = []
    for kind in cap.supported_waypoint_kinds:
        frame = _WAYPOINT_KIND_FROM_PROTO.get(kind)
        if frame is None:
            raise ValueError(f"unmapped waypoint kind {kind} in factsheet")
        frames.append(frame)
    return NavigationCapability(supported_waypoint_kinds=frames)


def factsheet_from_proto(proto: factsheet_pb2.Factsheet, robot_id: str) -> RobotFactsheet:
    """Rebuild the in-house factsheet; ``robot_id`` comes from the transport key."""
    navigation: NavigationCapability | None = None
    for cap in proto.stage_capabilities:
        if cap.WhichOneof("detail") != "navigation":
            continue  # empty entry, or a future kind the backend does not gate on
        if navigation is not None:
            raise ValueError("factsheet declares the NAVIGATION capability more than once")
        navigation = _navigation_from_proto(cap.navigation)
    return RobotFactsheet(robot_id=robot_id, navigation=navigation)
