"""Field aggregate root."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel
from pydantic import Field as PField


class Field(BaseModel):
    id: UUID
    name: str
    geometry: Polygon
    area_ha: float | None = PField(
        description="Computed by Postgres from geometry; null for empty or degenerate polygons"
    )
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
