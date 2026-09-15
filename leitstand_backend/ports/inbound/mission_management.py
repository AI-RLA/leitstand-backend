"""Driving port: operator commands on mission definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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


class CoveragePlanningFields(BaseModel):
    """What a coverage stage is planned from; the stage input and the preview query share it."""

    field_id: UUID | None = Field(default=None, description="Field to cover, from list_fields.")
    operation_width_m: float | None = Field(
        default=None, gt=0, description="Working width of the mounted implement, in metres."
    )
    params_robot_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Robot whose factsheet supplies the turning radius and track width; omit for values "
            "entered by hand."
        ),
    )
    turning_radius_m: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Minimum turning radius in metres. Required without params_robot_id; with one, it "
            "may only widen the robot's own."
        ),
    )
    headland_width_m: float | None = Field(
        default=None,
        ge=0,
        description="Turning space kept inside the field; the radius when omitted.",
    )
    swath_angle_deg: float | None = Field(
        default=None,
        ge=0,
        lt=180,
        description="Bearing of the swaths; the planner chooses when omitted.",
    )
    allow_overlap: bool = Field(
        default=False, description="Whether the last pass may overlap the one before it."
    )

    @model_validator(mode="after")
    def _robot_or_radius(self) -> CoveragePlanningFields:
        if (
            self.field_id is not None
            and self.params_robot_id is None
            and self.turning_radius_m is None
        ):
            raise ValueError("planning a coverage stage needs params_robot_id or turning_radius_m")
        return self


class CoverageStageInput(CoveragePlanningFields):
    """A coverage stage: carried unchanged when only ``stage_id`` is given, planned otherwise.

    The path is never written here; the backend plans it from the field and the machine values.
    ``stage_id`` keeps the stage's identity across a re-plan, so its runs stay comparable.
    """

    kind: Literal["coverage"] = "coverage"
    stage_id: UUID | None = None
    on_cancel: list["StageInput"] | None = Field(
        default=None,
        description=(
            "Cleanup stages executed sequentially when this stage is cancelled. "
            "Cleanup stages are themselves non-cancellable."
        ),
    )

    @property
    def carries(self) -> bool:
        return self.field_id is None

    @model_validator(mode="after")
    def _carry_or_plan(self) -> CoverageStageInput:
        if self.carries:
            if self.stage_id is None:
                raise ValueError(
                    "a coverage stage needs a stage_id to carry, or field_id and "
                    "operation_width_m to plan"
                )
            given = (
                self.operation_width_m,
                self.params_robot_id,
                self.turning_radius_m,
                self.headland_width_m,
                self.swath_angle_deg,
            )
            if any(v is not None for v in given) or self.allow_overlap:
                raise ValueError("a carried coverage stage takes no planning inputs")
            return self
        if self.operation_width_m is None:
            raise ValueError("planning a coverage stage needs operation_width_m")
        return self


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


class DeleteMissionCommand(BaseModel):
    mission_id: UUID


class RestoreMissionCommand(BaseModel):
    mission_id: UUID


class CreateGeneratedMissionCommand(BaseModel):
    """The assistant's coverage route: a mission from stages a planner already produced."""

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
