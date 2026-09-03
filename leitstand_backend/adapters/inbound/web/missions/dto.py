"""Wire DTOs for /api/v1/missions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PField

from leitstand_backend.domain.model.mission.coverage import CoverageProvenance
from leitstand_backend.domain.model.mission.mission import MissionStatus, Stage
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.ports.inbound.mission_management import StageInput


class MissionCreate(BaseModel):
    name: str = PField(min_length=1, max_length=255)
    description: str | None = None
    stages: list[StageInput] = PField(min_length=1)


class MissionCoverageCreate(BaseModel):
    """Request a coverage mission over a stored field.

    Deliberately flat and geometry-free: every value is a number or an id the caller already has,
    and the path itself comes from the field's own boundary.
    """

    # An unknown field is refused rather than dropped, so a caller that invents one (the turning
    # radius, which lives on the robot rather than in this request, is the one they reach for)
    # cannot read the 200 as proof it was applied.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    field_id: UUID = PField(description="Field to cover, from list_fields.")
    robot_id: str = PField(min_length=1, description="Robot the plan is computed for.")
    name: str | None = PField(
        default=None,
        min_length=1,
        max_length=255,
        description="Mission name. Defaults to the field's own name; do not ask for one.",
    )
    description: str | None = None
    operation_width_m: float = PField(
        gt=0,
        description=(
            "Working width of the mounted implement, in metres, and the one value only the "
            "operator knows. It is not the robot's own width: an implement is usually wider than "
            "the machine carrying it, and a plan built from the machine's width covers the field "
            "in far too many passes. Never take it from a robot's declared width; ask."
        ),
    )
    headland_width_m: float | None = PField(
        default=None,
        ge=0,
        description=(
            "Turning space kept inside the field, in metres. Omit it unless the operator states "
            "one: left open, the planner uses the robot's own turning radius, which is what a "
            "turn needs to stay inside the boundary."
        ),
    )
    swath_angle_deg: float | None = PField(
        default=None,
        ge=0,
        lt=180,
        description="Bearing of the swath lines; the planner chooses one when omitted.",
    )
    allow_overlap: bool = PField(
        default=False,
        description=(
            "Whether the last pass may overlap the one before it. A field is rarely a whole "
            "number of passes wide; leaving the remainder unworked is the alternative. Set it "
            "false only when working ground twice is worse than missing a strip."
        ),
    )
    replaces: UUID | None = PField(
        default=None,
        description=(
            "A coverage mission over the same field that this plan supersedes; it is deleted "
            "once the new one exists. Pass it when the operator wants an existing plan changed "
            "rather than a second one, because a plan is re-derived rather than edited."
        ),
    )


class MissionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    stages: list[StageInput] | None = None


class MissionAssignBody(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionDispatchBody(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionView(BaseModel):
    mission_id: UUID
    update_id: int
    name: str
    description: str | None
    stages: list[Stage]
    status: MissionStatus
    robot_id: str | None
    dispatched_at: datetime | None
    created_at: datetime
    updated_at: datetime
    failure_errors: list[MissionError] | None = None
    coverage: CoverageProvenance | None = None
