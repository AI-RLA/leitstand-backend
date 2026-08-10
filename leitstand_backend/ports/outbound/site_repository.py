"""Site persistence port."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.site import Site


class SiteRepository(ABC):
    @abstractmethod
    async def list(self) -> list[Site]: ...

    @abstractmethod
    async def get(self, site_id: UUID) -> Site | None: ...

    @abstractmethod
    async def create(
        self,
        name: str,
        anchor_lat: float,
        anchor_lon: float,
        anchor_heading_deg: float,
        nav2_map_ref: str,
        outline: Polygon | None,
        description: str | None,
    ) -> Site: ...

    @abstractmethod
    async def update(
        self,
        site_id: UUID,
        name: str | None = None,
        anchor_lat: float | None = None,
        anchor_lon: float | None = None,
        anchor_heading_deg: float | None = None,
        nav2_map_ref: str | None = None,
        outline: Polygon | None = None,
        description: str | None = None,
    ) -> Site | None: ...

    @abstractmethod
    async def delete(self, site_id: UUID) -> bool:
        """Hard-delete. Raises :class:`SiteInUse` if any non-terminal mission references the site."""
