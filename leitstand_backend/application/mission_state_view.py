"""Read DTO for a mission's per-stage state, served identically on REST and the live WS frame."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.stage_state_record import (
    StageStateRecord,
    attribute_errors,
)
from leitstand_backend.domain.model.mission.stage_status import StageStatus


class StageStateView(BaseModel):
    """One stage's runtime state plus the errors attributed to it."""

    stage_id: UUID
    stage_index: int
    status: StageStatus
    progress: float
    started_at: datetime | None
    ended_at: datetime | None
    result: dict[str, str] | None
    errors: list[MissionError]


class MissionStateView(BaseModel):
    """A mission's per-stage state as one read shape for both REST and the live WS frame."""

    mission_id: UUID
    stage_states: list[StageStateView]


def build_mission_state_view(
    mission_id: UUID,
    stage_states: list[StageStateRecord],
    failure_errors: list[MissionError] | None,
) -> MissionStateView:
    """Compose the read view from per-stage records and the mission's failure errors.

    Each failure error is attributed to its stage so the timeline can render a stage's
    errors directly; the complete list stays on the mission (``MissionView.failure_errors``).
    """
    errors_by_stage = attribute_errors(failure_errors, stage_states)
    return MissionStateView(
        mission_id=mission_id,
        stage_states=[
            StageStateView(
                stage_id=record.stage_id,
                stage_index=record.stage_index,
                status=record.status,
                progress=record.progress,
                started_at=record.started_at,
                ended_at=record.ended_at,
                result=record.result,
                errors=errors_by_stage.get(record.stage_id, []),
            )
            for record in stage_states
        ],
    )
