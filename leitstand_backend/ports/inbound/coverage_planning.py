"""Driving port: plan a coverage mission over a field."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from leitstand_backend.domain.model.mission.mission import Mission


class PlanCoverageCommand(BaseModel):
    """Plan a path over a field's own boundary and create it as a draft mission.

    Carries no geometry, deliberately: the boundary comes from the stored field, so a caller
    cannot supply waypoints here and the path is derived from the one authoritative source. It
    carries no turning radius either, for the same reason applied to the robot: the machine
    declares that itself.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    field_id: UUID
    robot_id: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    operation_width_m: float = Field(gt=0)
    headland_width_m: float | None = Field(default=None, ge=0)
    swath_angle_deg: float | None = Field(default=None, ge=0, lt=180)
    allow_overlap: bool = Field(
        default=False,
        description="Overlap the last pass to cover the remainder rather than leave it unworked.",
    )
    replan: UUID | None = Field(
        default=None,
        description=(
            "Re-plan this coverage stage in place instead of creating a mission. Its mission "
            "keeps its id, its other stages and its past runs; only this stage's path and "
            "provenance change, and it keeps its own id so its history joins up."
        ),
    )


class CoveragePlanningUseCase(ABC):
    @abstractmethod
    async def plan(self, command: PlanCoverageCommand) -> Mission: ...
