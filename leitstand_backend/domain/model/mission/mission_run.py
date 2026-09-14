"""MissionRun: one execution of a mission, with the plan it executed frozen alongside."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_status import RunStatus


class RunOrigin(BaseModel):
    """Who caused a run: a person acting directly, or the assistant through an approved tool call.

    A readable copy of what the audit row records for the same action, kept on the run because the
    audit log has no read path.
    """

    kind: Literal["manual", "agent"]
    actor: str = Field(min_length=1)
    tool_call_id: str | None = None


class MissionRunSummary(BaseModel):
    """A run without its plan: what a list of runs shows."""

    run_id: UUID
    mission_id: UUID
    robot_id: str = Field(min_length=1)
    status: RunStatus
    stages_digest: str = Field(min_length=1)
    origin: RunOrigin
    notes: str | None = None
    created_at: datetime
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_frame_at: datetime | None = None
    updated_at: datetime


class RunSiteAnchor(BaseModel):
    """Where a site stood when a run was dispatched into it.

    A site-local waypoint is a pair of numbers that mean nothing without the frame they are
    measured in, so the frame is copied into the run beside the plan. The name is copied
    too, so the run stays readable after the site is deleted.
    """

    name: str
    anchor_lat: float = Field(ge=-90, le=90)
    anchor_lon: float = Field(ge=-180, le=180)
    anchor_heading_deg: float = Field(ge=0, lt=360)


class MissionRun(MissionRunSummary):
    """A run with the stages it was dispatched with, frozen as they stood.

    ``stages`` is a copy taken at dispatch, carrying each coverage stage's own provenance, so
    editing or re-planning the mission afterwards never changes it; neither does moving or
    deleting a site it drove in, because ``site_anchors`` is copied beside it.
    """

    stages: list[Stage] = Field(min_length=1)
    site_anchors: dict[str, RunSiteAnchor] | None = None
    failure_errors: list[MissionError] | None = None
