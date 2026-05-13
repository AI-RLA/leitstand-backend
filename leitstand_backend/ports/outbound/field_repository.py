"""Field persistence Port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.field import Field


class FieldRepository(ABC):
    @abstractmethod
    async def list(self) -> list[Field]: ...

    @abstractmethod
    async def get(self, field_id: UUID) -> Field | None: ...

    @abstractmethod
    async def create(
        self,
        name: str,
        geometry: Polygon,
        notes: str | None,
    ) -> Field: ...

    @abstractmethod
    async def update(
        self,
        field_id: UUID,
        name: str | None = None,
        geometry: Polygon | None = None,
        notes: str | None = None,
    ) -> Field | None: ...

    @abstractmethod
    async def delete(self, field_id: UUID) -> bool:
        """Hard-delete. Returns True if a row was deleted."""
