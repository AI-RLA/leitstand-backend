"""Wire DTOs for /api/v1/sites."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel
from pydantic import Field as PField


class SiteCreate(BaseModel):
    name: str = PField(min_length=1, max_length=255)
    anchor_lat: float = PField(ge=-90, le=90)
    anchor_lon: float = PField(ge=-180, le=180)
    anchor_heading_deg: float = PField(ge=-180, le=180)
    nav2_map_ref: str = PField(min_length=1)
    outline: Polygon | None = None
    description: str | None = None


class SiteUpdate(BaseModel):
    name: str | None = None
    anchor_lat: float | None = PField(default=None, ge=-90, le=90)
    anchor_lon: float | None = PField(default=None, ge=-180, le=180)
    anchor_heading_deg: float | None = PField(default=None, ge=-180, le=180)
    nav2_map_ref: str | None = None
    outline: Polygon | None = None
    description: str | None = None


class SiteView(BaseModel):
    site_id: UUID
    name: str
    anchor_lat: float
    anchor_lon: float
    anchor_heading_deg: float
    nav2_map_ref: str
    outline: Polygon | None
    description: str | None
    created_at: datetime
    updated_at: datetime
