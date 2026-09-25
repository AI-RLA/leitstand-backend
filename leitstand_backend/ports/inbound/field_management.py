"""Driving port: operator commands on fields."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.field import Field, WGS84Polygon


class CreateFieldCommand(BaseModel):
    name: str = PField(min_length=1, max_length=200)
    geometry: WGS84Polygon
    notes: str | None = None


class UpdateFieldCommand(BaseModel):
    field_id: UUID
    name: str | None = None
    geometry: WGS84Polygon | None = None
    notes: str | None = None


class DeleteFieldCommand(BaseModel):
    field_id: UUID


class FieldManagementUseCase(ABC):
    @abstractmethod
    async def create(self, command: CreateFieldCommand) -> Field: ...

    @abstractmethod
    async def update(self, command: UpdateFieldCommand) -> Field: ...

    @abstractmethod
    async def delete(self, command: DeleteFieldCommand) -> None: ...

    @abstractmethod
    async def get(self, field_id: UUID) -> Field | None: ...

    @abstractmethod
    async def list(self) -> list[Field]: ...
