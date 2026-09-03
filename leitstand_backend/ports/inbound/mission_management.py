"""Driving port: operator commands on missions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import Mission
from leitstand_backend.domain.model.mission.waypoint import Waypoint


class NavigationStageInput(BaseModel):
    """Drive the robot through an ordered list of waypoints, as requested by a caller.

    Identity is deliberately absent, and that is the point: ``stage_id`` is assigned by the
    backend, so no caller can choose one. Two stages sharing an id would collapse onto a single
    ``mission_stage_state`` row, leaving the robot's per-stage reports unattributable.
    """

    kind: Literal["navigation"] = "navigation"
    waypoints: list[Waypoint] = Field(
        min_length=1,
        description=(
            "Ordered waypoints to traverse. All waypoints in one stage must share "
            "their ``kind`` (homogeneity); this is enforced by the backend at "
            "dispatch, not by this schema."
        ),
    )
    on_cancel: list["StageInput"] | None = Field(
        default=None,
        description=(
            "Cleanup stages executed sequentially when this stage is cancelled. "
            "Cleanup stages are themselves non-cancellable."
        ),
    )


class SegmentInput(BaseModel):
    """One swath across a field, or the turn joining two of them."""

    kind: Literal["swath", "turn"]
    waypoints: list[Waypoint] = Field(min_length=2)


class CoverageStageInput(BaseModel):
    """Cover a field by driving its swaths, each reached by the turn before it.

    Identity is assigned by the backend, exactly as for a navigation stage.
    """

    kind: Literal["coverage"] = "coverage"
    segments: list[SegmentInput] = Field(min_length=1)
    on_cancel: list["StageInput"] | None = None


StageInput = Annotated[NavigationStageInput | CoverageStageInput, Field(discriminator="kind")]

NavigationStageInput.model_rebuild()
CoverageStageInput.model_rebuild()


class CreateMissionCommand(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stages: list[StageInput] = Field(min_length=1)


class UpdateMissionCommand(BaseModel):
    mission_id: UUID
    name: str | None = None
    description: str | None = None
    stages: list[StageInput] | None = None


class AssignMissionCommand(BaseModel):
    mission_id: UUID
    robot_id: str = Field(min_length=1)


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
