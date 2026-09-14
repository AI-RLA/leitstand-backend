"""Wire DTOs for /api/v1/missions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PField

from leitstand_backend.adapters.inbound.web.runs.dto import RunSummaryView
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
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
    replan: UUID | None = PField(
        default=None,
        description=(
            "The id of an existing coverage stage whose path should be re-planned in place, "
            "instead of creating a new mission. Pass it when the operator wants an existing plan "
            "changed rather than a second one: a plan is re-derived rather than edited. The "
            "mission keeps its id, its name, its other stages and every run it has had; only that "
            "stage's path and provenance change."
        ),
    )


class MissionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    stages: list[StageInput] | None = None


class MissionAssignBody(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionDispatchBody(BaseModel):
    robot_id: str | None = PField(
        default=None, min_length=1, description="Robot to run on; defaults to the assigned one."
    )
    notes: str | None = PField(default=None, max_length=4000)


class RunSelectBody(BaseModel):
    """Which run a mission-addressed command means; needed only when several are active."""

    run_id: UUID | None = None


class CancelBody(RunSelectBody):
    mode: CancelMode = PField(
        default=CancelMode.GRACEFUL,
        description=(
            "How the robot stops. Both stop within seconds: 'graceful' comes to a controlled stop "
            "at the next safe point (the current motion completed, the implement raised) and then "
            "runs the stage's on_cancel cleanup; 'immediate' stops at once, then cleans up."
        ),
    )


class MissionView(BaseModel):
    mission_id: UUID
    name: str
    description: str | None
    stages: list[Stage]
    assigned_robot_id: str | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stages_digest: str = PField(
        description=(
            "Digest of the stages as they stand now. A run whose stages_digest matches ran the "
            "plan this mission currently holds; one that differs ran an older plan. Computed the "
            "same way as a run's, so the two are directly comparable."
        ),
    )
    latest_run: RunSummaryView | None = PField(
        default=None,
        description="The most recently created run; null means the mission has never run.",
    )
    active_runs: list[RunSummaryView] = PField(
        default_factory=list,
        description=(
            "Every run of this mission still occupying a robot, newest first. Whether the "
            "mission is running is this being non-empty, not the status of latest_run: a "
            "concurrent run that ends first becomes latest_run while the other still drives. "
            "With more than one entry, mission-addressed cancel, pause and resume require a "
            "run_id, and these are the candidates."
        ),
    )
