"""Driving port: operator commands on sites."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.site import Site


class CreateSiteCommand(BaseModel):
    name: str = PField(min_length=1, max_length=255)
    anchor_lat: float = PField(ge=-90, le=90)
    anchor_lon: float = PField(ge=-180, le=180)
    anchor_heading_deg: float = PField(ge=-180, le=180)
    nav2_map_ref: str = PField(min_length=1)
    outline: Polygon | None = None
    description: str | None = None


class UpdateSiteCommand(BaseModel):
    site_id: UUID
    name: str | None = None
    anchor_lat: float | None = PField(default=None, ge=-90, le=90)
    anchor_lon: float | None = PField(default=None, ge=-180, le=180)
    anchor_heading_deg: float | None = PField(default=None, ge=-180, le=180)
    nav2_map_ref: str | None = None
    outline: Polygon | None = None
    description: str | None = None


class DeleteSiteCommand(BaseModel):
    site_id: UUID


class SiteManagementUseCase(ABC):
    @abstractmethod
    async def create(self, command: CreateSiteCommand) -> Site: ...

    @abstractmethod
    async def update(self, command: UpdateSiteCommand) -> Site: ...

    @abstractmethod
    async def delete(self, command: DeleteSiteCommand) -> None: ...

    @abstractmethod
    async def get(self, site_id: UUID) -> Site | None: ...

    @abstractmethod
    async def list(self) -> list[Site]: ...
