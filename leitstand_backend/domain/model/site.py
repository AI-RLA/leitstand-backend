"""Site: a named indoor/local-Cartesian frame anchored in WGS84."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel, Field


class Site(BaseModel):
    """A registered Site: local Cartesian frame plus its WGS84 anchor.

    A robot loads the Site's Nav2 occupancy grid (referenced by
    ``nav2_map_ref``) when entering site-local mode and uses the
    anchor to re-set its global ENU frame at the entry pose.
    """

    site_id: UUID
    name: str = Field(min_length=1, max_length=255)
    coordinate_frame: Literal["cartesian"] = "cartesian"

    anchor_lat: float = Field(
        ge=-90,
        le=90,
        description="WGS84 latitude (degrees) of the site frame origin.",
    )
    anchor_lon: float = Field(
        ge=-180,
        le=180,
        description="WGS84 longitude (degrees) of the site frame origin.",
    )
    anchor_heading_deg: float = Field(
        ge=-180,
        le=180,
        description=(
            "Orientation of the site's local +x axis in degrees, clockwise from true north."
        ),
    )

    nav2_map_ref: str = Field(
        min_length=1,
        description=(
            "Opaque identifier the robot resolves to a Nav2 occupancy-grid "
            "map (e.g. a slug used to look up a local .pgm file)."
        ),
    )

    outline: Polygon | None = Field(
        default=None,
        description=(
            "GeoJSON polygon footprint of the site for frontend rendering. "
            "Coordinates are WGS84 [longitude, latitude]."
        ),
    )
    description: str | None = None

    created_at: datetime
    updated_at: datetime
