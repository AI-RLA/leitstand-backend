"""Wire DTOs for runs: /api/v1/runs and /api/v1/missions/{id}/runs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_run import RunSiteAnchor
from leitstand_backend.domain.model.mission.mission_state import MissionError, MissionExecStatus
from leitstand_backend.domain.model.mission.run_lifecycle import Actor, RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus


class RunOriginView(BaseModel):
    kind: Literal["manual", "agent"]
    actor: str
    tool_call_id: str | None = None


class LastReportView(BaseModel):
    """The most recent state report the robot sent for the run."""

    received_at: datetime = PField(description="When the backend received it, by its own clock.")
    header_id: int = PField(
        description="The robot's report counter; later reports have higher values."
    )
    exec_status: MissionExecStatus
    robot_timestamp: datetime = PField(description="When the robot sent it, by the robot's clock.")


class RunTransitionView(BaseModel):
    """One status change of the run, in the order they happened."""

    from_status: RunStatus
    to_status: RunStatus
    trigger: RunTrigger
    at: datetime
    actor: Actor = PField(
        description="Who caused it: the operator's request, the robot's report, or the backend."
    )
    acknowledged: bool | None = PField(
        default=None,
        description=(
            "For an operator's request: true when the robot replied that it applied it, false "
            "when it refused or did not answer; null while unknown or for other transitions."
        ),
    )
    detail: dict | None = None


class RunSummaryView(BaseModel):
    """A run without its stages: what a list shows, and what a mission carries as ``latest_run``."""

    run_id: UUID
    mission_id: UUID
    robot_id: str
    status: RunStatus
    stages_digest: str = PField(
        description=(
            "Content digest of what this run was told to do, ignoring stage ids and how a "
            "stage was produced. Two runs with the same digest drove the same ground."
        )
    )
    origin: RunOriginView
    notes: str | None
    created_at: datetime
    dispatched_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    last_report: LastReportView | None = PField(
        description="The robot's most recent report on this run; null before the first one."
    )
    updated_at: datetime = PField(
        description="When the backend last changed this run: a status transition or a notes edit."
    )


class RunView(RunSummaryView):
    """A run with the stages it was dispatched with, frozen as they stood, and any failure."""

    stages: list[Stage]
    site_anchors: dict[str, RunSiteAnchor] | None = PField(
        default=None,
        description=(
            "The frame of each site this run drove in, as it stood at dispatch, keyed by site "
            "id. Site-local waypoints are measured against these rather than "
            "against the site as it is now, so moving or deleting a site cannot move a run "
            "that already happened."
        ),
    )
    failure_errors: list[MissionError] | None = None
    transitions: list[RunTransitionView] = PField(
        default_factory=list,
        description="The run's status changes so far, oldest first.",
    )


class RunNotesPatch(BaseModel):
    notes: str | None = PField(default=None, max_length=4000)
