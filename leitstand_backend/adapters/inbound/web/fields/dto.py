"""Wire DTOs for /api/v1/fields."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel


class FieldCreate(BaseModel):
    name: str
    geometry: Polygon
    notes: str | None = None


class FieldUpdate(BaseModel):
    name: str | None = None
    geometry: Polygon | None = None
    notes: str | None = None


class FieldView(BaseModel):
    id: UUID
    name: str
    geometry: Polygon
    area_ha: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
