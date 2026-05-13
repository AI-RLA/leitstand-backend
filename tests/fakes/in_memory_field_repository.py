"""In-memory FieldRepository fake (test-only)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.field import Field
from leitstand_backend.ports.outbound.field_repository import FieldRepository


class InMemoryFieldRepository(FieldRepository):
    def __init__(self) -> None:
        self._fields: dict[UUID, Field] = {}
        self._lock = threading.Lock()

    async def list(self) -> list[Field]:
        with self._lock:
            return sorted(self._fields.values(), key=lambda f: f.created_at, reverse=True)

    async def get(self, field_id: UUID) -> Field | None:
        with self._lock:
            return self._fields.get(field_id)

    async def create(
        self,
        name: str,
        geometry: Polygon,
        notes: str | None,
    ) -> Field:
        area = _polygon_area_ha(geometry)
        now = datetime.now(timezone.utc)
        field = Field(
            id=uuid4(),
            name=name,
            geometry=geometry,
            area_ha=area,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._fields[field.id] = field
        return field

    async def update(
        self,
        field_id: UUID,
        name: str | None = None,
        geometry: Polygon | None = None,
        notes: str | None = None,
    ) -> Field | None:
        with self._lock:
            current = self._fields.get(field_id)
            if current is None:
                return None
            updated = current.model_copy(
                update={
                    "name": name if name is not None else current.name,
                    "geometry": geometry if geometry is not None else current.geometry,
                    "notes": notes if notes is not None else current.notes,
                    "area_ha": _polygon_area_ha(geometry) if geometry else current.area_ha,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._fields[field_id] = updated
            return updated

    async def delete(self, field_id: UUID) -> bool:
        with self._lock:
            return self._fields.pop(field_id, None) is not None


def _polygon_area_ha(geometry: Polygon) -> float:
    try:
        from shapely.geometry import shape

        return float(shape(geometry.model_dump()).area * 12365.0)
    except ImportError:
        return 0.0
