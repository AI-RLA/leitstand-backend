"""Field aggregate root."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import AfterValidator, BaseModel
from pydantic import Field as PField


def _within_wgs84(polygon: Polygon) -> Polygon:
    """Refuse a polygon with a position outside WGS84 longitude and latitude."""
    for ring in polygon.coordinates:
        for position in ring:
            if not (-180 <= position[0] <= 180 and -90 <= position[1] <= 90):
                raise ValueError(
                    f"position {list(position)} lies outside WGS84 longitude and latitude"
                )
    return polygon


WGS84Polygon = Annotated[Polygon, AfterValidator(_within_wgs84)]


class Field(BaseModel):
    id: UUID
    name: str
    geometry: WGS84Polygon
    area_ha: float | None = PField(
        description="Derived from the geometry; null for an empty or degenerate polygon"
    )
    # Derived from the geometry when a field is read, so it needs no bounds of its own.
    center_lat: float | None = None
    center_lon: float | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
