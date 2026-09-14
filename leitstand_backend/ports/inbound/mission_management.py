"""Driving port: operator commands on mission definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import Mission, Stage
from leitstand_backend.domain.model.mission.waypoint import Waypoint


class NavigationStageInput(BaseModel):
    """Drive the robot through an ordered list of waypoints, as requested by a caller.

    Supply ``stage_id`` to keep a stage's identity across an edit, so its runs stay comparable;
    omit it for a new stage and the backend assigns one. A supplied id must already belong to the
    mission being edited and appear once in the request.
    """

    kind: Literal["navigation"] = "navigation"
    stage_id: UUID | None = None
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


class CoverageStageRef(BaseModel):
    """A coverage stage this mission already has, carried through an edit unchanged.

    Swaths are a planner's output, so there is no way to write one here: the stage is named, and
    the stored segments and provenance are kept as they are. Naming a stage of another mission,
    or one that is not coverage, is refused.
    """

    kind: Literal["coverage"] = "coverage"
    stage_id: UUID


StageInput = Annotated[NavigationStageInput | CoverageStageRef, Field(discriminator="kind")]

NavigationStageInput.model_rebuild()


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


class DeleteMissionCommand(BaseModel):
    mission_id: UUID


class RestoreMissionCommand(BaseModel):
    mission_id: UUID


class CreateGeneratedMissionCommand(BaseModel):
    """The planner's own create path, carrying stages it produced rather than stage inputs.

    A coverage stage cannot be expressed as a stage input at all, because its segments and its
    provenance are made together and only by a planner.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stages: list[Stage] = Field(min_length=1)


class ReplaceGeneratedStageCommand(BaseModel):
    """Overwrite one generated stage in place, keeping its id and the mission's other stages."""

    mission_id: UUID
    stage: Stage


class MissionManagementUseCase(ABC):
    """Operator commands on mission definitions. Running one is the run use case's job."""

    @abstractmethod
    async def create(self, command: CreateMissionCommand) -> Mission: ...

    @abstractmethod
    async def update(self, command: UpdateMissionCommand) -> Mission: ...

    @abstractmethod
    async def assign(self, command: AssignMissionCommand) -> Mission: ...

    @abstractmethod
    async def unassign(self, command: UnassignMissionCommand) -> Mission: ...

    @abstractmethod
    async def delete(self, command: DeleteMissionCommand) -> None:
        """Delete a mission that never ran; archive one that did; refuse one with an active run."""

    @abstractmethod
    async def restore(self, command: RestoreMissionCommand) -> Mission: ...

    @abstractmethod
    async def create_generated(self, command: CreateGeneratedMissionCommand) -> Mission: ...

    @abstractmethod
    async def replace_generated_stage(self, command: ReplaceGeneratedStageCommand) -> Mission: ...

    @abstractmethod
    async def get(self, mission_id: UUID) -> Mission | None: ...

    @abstractmethod
    async def list(self, *, include_archived: bool = False) -> list[Mission]: ...
