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


class PhysicalParameters(BaseModel):
    """Fixed physical properties of the machine, declared by the robot itself."""

    track_width_m: float = Field(
        gt=0, description="Distance between the wheels in metres, centre to centre."
    )
    min_turning_radius_m: float = Field(
        ge=0, description="Zero declares a robot that turns on the spot."
    )


class CoverageCapability(BaseModel):
    """Capabilities specific to executing COVERAGE stages.

    A claim about steering rather than geometry: that the machine holds the swath line between
    its endpoints instead of taking any convenient path between them.
    """

    supported_waypoint_kinds: list[WaypointKind] = Field(default_factory=list)


class RobotFactsheet(BaseModel):
    """What a robot can do, cached by the backend to pre-flight-validate dispatch.

    A present per-kind capability (``navigation``) declares the robot supports
    that stage kind. ``robot_id`` is stamped from the transport key on ingest,
    not carried in the wire payload.
    """

    robot_id: str = Field(min_length=1)
    navigation: NavigationCapability | None = None
    coverage: CoverageCapability | None = None
    physical_parameters: PhysicalParameters | None = None
