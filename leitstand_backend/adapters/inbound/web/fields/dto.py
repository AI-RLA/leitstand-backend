"""Wire DTOs for /api/v1/fields."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel, Field

from leitstand_backend.domain.model.field import WGS84Polygon


class FieldCreate(BaseModel):
    name: str
    geometry: WGS84Polygon
    notes: str | None = None


class FieldUpdate(BaseModel):
    name: str | None = None
    geometry: WGS84Polygon | None = None
    notes: str | None = None


class FieldView(BaseModel):
    id: UUID
    name: str
    geometry: Polygon
    area_ha: float | None
    center_lat: float | None = Field(
        description="Degrees latitude, WGS84, of a point guaranteed to lie inside the field, not its centroid. Null for a polygon with no ring."
    )
    center_lon: float | None = Field(
        description="Degrees longitude, WGS84, of a point guaranteed to lie inside the field, not its centroid. Null for a polygon with no ring."
    )
    notes: str | None
    created_at: datetime
    updated_at: datetime
