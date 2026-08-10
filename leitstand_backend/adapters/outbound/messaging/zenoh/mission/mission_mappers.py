"""Named-field mappers between the mission domain model and the robot proto contract.

The anti-corruption layer for the dispatch direction: proto types never leave
the zenoh adapters, and every field is mapped explicitly so contract drift
fails loudly here instead of corrupting a mission silently. The proto Mission
is a projection ({mission_id, stages}); backend bookkeeping (name, timestamps,
lifecycle) never reaches the robot wire.
"""

from __future__ import annotations

from uuid import UUID

from leitstand.robot.v1 import mission_pb2

from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage, Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.domain.model.mission.waypoint import (
    SiteLocalWaypoint,
    Waypoint,
    WGS84Waypoint,
)

_CANCEL_MODE_TO_PROTO: dict[CancelMode, mission_pb2.CancelMode] = {
    CancelMode.GRACEFUL: mission_pb2.CANCEL_MODE_GRACEFUL,
    CancelMode.IMMEDIATE: mission_pb2.CANCEL_MODE_IMMEDIATE,
}


def _waypoint_to_proto(waypoint: Waypoint) -> mission_pb2.Waypoint:
    """Map a domain waypoint onto the matching proto oneof arm."""
    if isinstance(waypoint, WGS84Waypoint):
        wgs84 = mission_pb2.WGS84Waypoint(lat=waypoint.lat, lon=waypoint.lon)
        if waypoint.heading_deg is not None:
            wgs84.heading_deg = waypoint.heading_deg
        return mission_pb2.Waypoint(wgs84=wgs84)
    if isinstance(waypoint, SiteLocalWaypoint):
        site_local = mission_pb2.SiteLocalWaypoint(
            site_id=str(waypoint.site_id), x=waypoint.x, y=waypoint.y
        )
        if waypoint.theta is not None:
            site_local.theta = waypoint.theta
        return mission_pb2.Waypoint(site_local=site_local)
    raise TypeError(f"unmapped waypoint type: {type(waypoint).__name__}")


def _stage_to_proto(stage: Stage) -> mission_pb2.Stage:
    """Build a proto Stage with kind and the matching payload arm set together."""
    # Stage is NavigationStage today; the explicit check keeps a future unmapped
    # kind a loud TypeError here instead of a mis-tagged payload on the wire.
    if not isinstance(stage, NavigationStage):
        raise TypeError(f"unmapped stage type: {type(stage).__name__}")
    return mission_pb2.Stage(
        stage_id=str(stage.stage_id),
        kind=mission_pb2.STAGE_KIND_NAVIGATION,
        navigation=mission_pb2.NavigationStage(
            waypoints=[_waypoint_to_proto(w) for w in stage.waypoints]
        ),
        on_cancel=[_stage_to_proto(s) for s in (stage.on_cancel or [])],
    )


def mission_to_proto(mission: Mission) -> mission_pb2.Mission:
    """Project a domain mission onto the robot wire shape."""
    return mission_pb2.Mission(
        mission_id=str(mission.mission_id),
        stages=[_stage_to_proto(s) for s in mission.stages],
    )


def dispatch_request_to_proto(
    mission: Mission, dispatch_id: str
) -> mission_pb2.MissionDispatchRequest:
    """Build the dispatch envelope carrying the mission projection."""
    return mission_pb2.MissionDispatchRequest(
        dispatch_id=dispatch_id, mission=mission_to_proto(mission)
    )


def dispatch_response_from_proto(
    response: mission_pb2.MissionDispatchResponse,
) -> tuple[bool, str | None]:
    """Read a dispatch reply; absent reason maps to None."""
    reason = response.reason if response.HasField("reason") else None
    return response.accepted, reason


def cancel_to_proto(mission_id: UUID, mode: CancelMode) -> mission_pb2.CancelRequest:
    """Build the cancel envelope for one mission."""
    return mission_pb2.CancelRequest(mission_id=str(mission_id), mode=_CANCEL_MODE_TO_PROTO[mode])


def _waypoint_from_proto(waypoint: mission_pb2.Waypoint) -> WGS84Waypoint | SiteLocalWaypoint:
    """Rebuild a domain waypoint from the set oneof arm; reject an empty union."""
    arm = waypoint.WhichOneof("kind")
    if arm == "wgs84":
        wgs84 = waypoint.wgs84
        return WGS84Waypoint(
            lat=wgs84.lat,
            lon=wgs84.lon,
            heading_deg=wgs84.heading_deg if wgs84.HasField("heading_deg") else None,
        )
    if arm == "site_local":
        site_local = waypoint.site_local
        return SiteLocalWaypoint(
            site_id=UUID(site_local.site_id),
            x=site_local.x,
            y=site_local.y,
            theta=site_local.theta if site_local.HasField("theta") else None,
        )
    raise ValueError(f"waypoint has no known frame arm set (got {arm!r})")


def _stage_from_proto(stage: mission_pb2.Stage) -> Stage:
    """Rebuild a domain stage; reject unknown kinds and kind/payload mismatches."""
    if stage.kind != mission_pb2.STAGE_KIND_NAVIGATION:
        raise ValueError(f"unsupported stage kind {stage.kind} for stage {stage.stage_id}")
    if stage.WhichOneof("payload") != "navigation":
        raise ValueError(
            f"stage {stage.stage_id}: kind NAVIGATION does not match payload arm "
            f"{stage.WhichOneof('payload')!r}"
        )
    return NavigationStage(
        stage_id=UUID(stage.stage_id),
        waypoints=[_waypoint_from_proto(w) for w in stage.navigation.waypoints],
        on_cancel=[_stage_from_proto(s) for s in stage.on_cancel] or None,
    )


def mission_projection_from_proto(mission: mission_pb2.Mission) -> tuple[UUID, list[Stage]]:
    """Rebuild the projected fields from a proto mission.

    The wire carries only the projection, so a full domain ``Mission`` (name,
    timestamps) is never reconstructed from it; this inverse exists for the
    contract round-trip test and for receiver-side validation.
    """
    return UUID(mission.mission_id), [_stage_from_proto(s) for s in mission.stages]
