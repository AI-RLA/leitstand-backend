"""Driving port: operator commands on missions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.mission.mission import Mission, Stage


class CreateMissionCommand(BaseModel):
    name: str = PField(min_length=1, max_length=255)
    description: str | None = None
    stages: list[Stage] = PField(min_length=1)


class UpdateMissionCommand(BaseModel):
    mission_id: UUID
    name: str | None = None
    description: str | None = None
    stages: list[Stage] | None = None


class AssignMissionCommand(BaseModel):
    mission_id: UUID
    robot_id: str = PField(min_length=1)


class UnassignMissionCommand(BaseModel):
    mission_id: UUID


class DispatchMissionCommand(BaseModel):
    mission_id: UUID
    robot_id: str | None = None


class CancelMissionCommand(BaseModel):
    mission_id: UUID


class PauseMissionCommand(BaseModel):
    mission_id: UUID


class ResumeMissionCommand(BaseModel):
    mission_id: UUID


class DeleteMissionCommand(BaseModel):
    mission_id: UUID


class ResetMissionCommand(BaseModel):
    mission_id: UUID


class MissionManagementUseCase(ABC):
    @abstractmethod
    async def create(self, command: CreateMissionCommand) -> Mission: ...

    @abstractmethod
    async def update(self, command: UpdateMissionCommand) -> Mission: ...

    @abstractmethod
    async def assign(self, command: AssignMissionCommand) -> Mission: ...

    @abstractmethod
    async def unassign(self, command: UnassignMissionCommand) -> Mission: ...

    @abstractmethod
    async def dispatch(self, command: DispatchMissionCommand) -> Mission: ...

    @abstractmethod
    async def cancel(self, command: CancelMissionCommand) -> Mission: ...

    @abstractmethod
    async def pause(self, command: PauseMissionCommand) -> Mission: ...

    @abstractmethod
    async def resume(self, command: ResumeMissionCommand) -> Mission: ...

    @abstractmethod
    async def delete(self, command: DeleteMissionCommand) -> None: ...

    @abstractmethod
    async def reset(self, command: ResetMissionCommand) -> Mission: ...

    @abstractmethod
    async def get(self, mission_id: UUID) -> Mission | None: ...

    @abstractmethod
    async def list(self) -> list[Mission]: ...
