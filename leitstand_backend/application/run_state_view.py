"""A run's per-stage state: the read DTO, and the one way it is resolved and published."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission_run import MissionRun
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import (
    StageStateRecord,
    StatusSource,
    attribute_errors,
    final_stage_statuses,
)
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from leitstand_backend.ports.outbound.event_publisher import EventPublisher, mission_topic
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository


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
    status_source: StatusSource = Field(
        description=(
            "Who established the status: 'robot' when the robot reported it, 'backend' when the "
            "backend projected it because the run ended before the robot reported this stage."
        )
    )
    parent_stage_id: UUID | None = Field(
        default=None,
        description="Set on a cleanup stage: the stage whose cancel started it.",
    )


class RunStateView(BaseModel):
    """A run's per-stage state as one read shape for both REST and the live WS frame.

    Carries both ids: the WS topic is keyed by mission, so a subscriber showing one run must
    filter frames by ``run_id``, and a list of missions indexes them by ``mission_id``.
    """

    run_id: UUID
    mission_id: UUID
    stage_states: list[StageStateView]


def build_run_state_view(
    run_id: UUID,
    mission_id: UUID,
    stage_states: list[StageStateRecord],
    failure_errors: list[MissionError] | None,
) -> RunStateView:
    """Compose the read view from per-stage records and the run's failure errors.

    Each failure error is attributed to its stage so the timeline can render a stage's errors
    directly; the complete list stays on the run.
    """
    errors_by_stage = attribute_errors(failure_errors, stage_states)
    return RunStateView(
        run_id=run_id,
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
                status_source=record.status_source,
                parent_stage_id=record.parent_stage_id,
            )
            for record in stage_states
        ],
    )


def publish_run_state(
    events: EventPublisher,
    run: MissionRun,
    records: list[StageStateRecord],
    failure_errors: list[MissionError] | None,
) -> None:
    """Latch the run's per-stage state on its mission's topic.

    Latched because a subscriber joining later must see the current state rather than wait for
    the next frame; the view carries ``run_id`` so it can tell which run it is looking at.
    """
    events.publish(
        mission_topic(run.mission_id, "state"),
        build_run_state_view(run.run_id, run.mission_id, records, failure_errors).model_dump(
            mode="json"
        ),
        latch=True,
    )


async def settle_stage_state(
    runs: MissionRunRepository,
    events: EventPublisher,
    run: MissionRun,
    status: RunStatus,
    failure_errors: list[MissionError] | None = None,
    overlay: list[StageStateRecord] | None = None,
) -> list[StageStateRecord]:
    """Resolve, persist and publish every stage's final state, and return it.

    The one place a run's stages are closed out. ``overlay`` carries the arriving frame's
    records when the run ends on the robot's own report.
    """
    stored = {record.stage_id: record for record in await runs.get_stage_runs(run.run_id)}
    if overlay:
        stored.update({record.stage_id: record for record in overlay})
    resolved = final_stage_statuses(run.stages, stored, status, datetime.now(timezone.utc))
    await runs.overwrite_stage_runs(run.run_id, resolved)
    publish_run_state(events, run, resolved, failure_errors)
    return resolved
