"""Waypoint discriminated union: WGS84 (outdoor) and site-local (indoor)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class WGS84Waypoint(BaseModel):
    """A waypoint anchored in the WGS84 geographic frame."""

    kind: Literal["wgs84"] = "wgs84"
    lat: float = Field(ge=-90, le=90, description="Degrees latitude, WGS84.")
    lon: float = Field(ge=-180, le=180, description="Degrees longitude, WGS84.")
    heading_deg: float | None = Field(
        default=None,
        ge=0,
        lt=360,
        description="Target robot heading in degrees clockwise from true north (compass bearing), [0, 360).",
    )


class SiteLocalWaypoint(BaseModel):
    """A waypoint expressed in a Site's local Cartesian frame."""

    kind: Literal["site_local"] = "site_local"
    site_id: UUID = Field(description="Identifies the Site whose local frame this waypoint uses.")
    x: float = Field(description="Meters along the site's local +x axis.")
    y: float = Field(description="Meters along the site's local +y axis.")
    theta: float | None = Field(
        default=None,
        description="Target robot heading in radians, CCW from the site's local +x axis.",
    )


Waypoint = Annotated[
    WGS84Waypoint | SiteLocalWaypoint,
    Field(discriminator="kind"),
]
