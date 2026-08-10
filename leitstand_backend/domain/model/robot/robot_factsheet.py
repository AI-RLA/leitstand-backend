"""Robot factsheet: the robot's capability declaration, cached to pre-validate dispatch."""

from enum import Enum

from pydantic import BaseModel, Field


class WaypointKind(str, Enum):
    """A coordinate frame a waypoint can use; mirrors the Waypoint variants."""

    WGS84 = "wgs84"
    SITE_LOCAL = "site_local"


class NavigationCapability(BaseModel):
    """Capabilities specific to executing NAVIGATION stages."""

    supported_waypoint_kinds: list[WaypointKind] = Field(default_factory=list)


class RobotFactsheet(BaseModel):
    """What a robot can do, cached by the backend to pre-flight-validate dispatch.

    A present per-kind capability (``navigation``) declares the robot supports
    that stage kind. ``robot_id`` is stamped from the transport key on ingest,
    not carried in the wire payload.
    """

    robot_id: str = Field(min_length=1)
    navigation: NavigationCapability | None = None
