"""Per-stage runtime state: the persisted record, the terminal projection, error attribution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_status import StageStatus

_STAGE_ID_REF_KEY = "stage_id"

# Who set a stage's status: the robot reported it, or the backend projected it because the
# run ended before the robot reported that stage.
StatusSource = Literal["robot", "backend"]


class StageStateRecord(BaseModel):
    """One stage's current runtime state within a run, one record per stage.

    Distinct from the stage definition on the mission. ``header_id`` is the robot's report
    counter, so an older or reordered report never overwrites a newer one; ``source_ts`` is the
    report's robot-clock time.
    """

    stage_id: UUID
    header_id: int = 0
    stage_index: int
    status: StageStatus
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: dict[str, str] | None = None
    source_ts: datetime
    status_source: StatusSource = "robot"
    # Set on a cleanup stage's record: the stage whose cancel started it.
    parent_stage_id: UUID | None = None


def _project_status(status: StageStatus, run_status: RunStatus) -> StageStatus:
    """Project one stage's live status onto its final status given the run's outcome."""
    if status in (StageStatus.CANCELLED, StageStatus.SKIPPED):
        # The robot said so itself; nothing to project.
        return status
    if run_status is RunStatus.SUCCEEDED:
        return StageStatus.FINISHED
    # The run ended on FAILED, CANCELLED or REJECTED.
    if status is StageStatus.FINISHED:
        return StageStatus.FINISHED
    if status is StageStatus.FAILED:
        return StageStatus.FAILED
    if status is StageStatus.WAITING:
        return StageStatus.SKIPPED
    # An in-flight stage (INITIALIZING / RUNNING / PAUSED) inherits the mission's reason:
    # a commanded cancel makes it CANCELLED, anything else a genuine failure.
    return StageStatus.CANCELLED if run_status is RunStatus.CANCELLED else StageStatus.FAILED


def waiting_stage_statuses(stages: Sequence[Stage], when: datetime) -> list["StageStateRecord"]:
    """Project every stage as WAITING, for a run that has reported nothing yet.

    A run exists before its robot has said anything, and a caller asking for its state wants the
    plan it is about to drive rather than an empty list.
    """
    return [
        StageStateRecord(
            stage_id=stage.stage_id,
            stage_index=index,
            status=StageStatus.WAITING,
            source_ts=when,
            status_source="backend",
        )
        for index, stage in enumerate(stages)
    ]


def final_stage_statuses(
    stages: list[Stage],
    live_by_id: dict[UUID, StageStateRecord],
    run_status: RunStatus,
    now: datetime,
) -> list[StageStateRecord]:
    """Resolve every defined stage's final status when a run reaches a terminal state.

    The stage definitions are the spine: each is matched to its live record by ``stage_id``
    (WAITING when none was reported) and projected by :func:`_project_status`, so the result
    is complete however much the robot reported. A never-started stage keeps ``ended_at = None``,
    so it shows no duration. Cleanup stages the robot reported are kept as reported, after their
    parent; nothing is projected for cleanup the robot never ran.
    """
    resolved: list[StageStateRecord] = []
    for index, stage in enumerate(stages):
        live = live_by_id.get(stage.stage_id)
        current = live.status if live is not None else StageStatus.WAITING
        final = _project_status(current, run_status)
        # A status the robot reported keeps its source; one the projection changed, or set for a
        # stage never reported, is marked as the backend's.
        source: StatusSource = (
            live.status_source if live is not None and final is live.status else "backend"
        )
        started_at = live.started_at if live is not None else None
        ended_at = live.ended_at if live is not None else None
        if ended_at is None and started_at is not None and final is not StageStatus.SKIPPED:
            ended_at = now
        resolved.append(
            StageStateRecord(
                stage_id=stage.stage_id,
                header_id=live.header_id if live is not None else 0,
                stage_index=index,
                status=final,
                progress=live.progress if live is not None else 0.0,
                started_at=started_at,
                ended_at=ended_at,
                result=live.result if live is not None else None,
                source_ts=now,
                status_source=source,
            )
        )
        resolved.extend(
            record for record in live_by_id.values() if record.parent_stage_id == stage.stage_id
        )
    return resolved


def attribute_errors(
    failure_errors: list[MissionError] | None,
    resolved: list[StageStateRecord],
) -> dict[UUID, list[MissionError]]:
    """Map each mission-level failure error to the stage it belongs to.

    An error carrying a ``stage_id`` reference attaches to that stage. An unreferenced
    error attaches to the failing stage when exactly one stage failed; when the cause
    is ambiguous (no single failed stage) it stays mission-level and is omitted here,
    remaining visible on the mission's complete failure list.
    """
    by_stage: dict[UUID, list[MissionError]] = {}
    if not failure_errors:
        return by_stage
    stage_ids = {record.stage_id for record in resolved}
    failed = [record.stage_id for record in resolved if record.status is StageStatus.FAILED]
    sole_failed = failed[0] if len(failed) == 1 else None
    for error in failure_errors:
        target = _referenced_stage_id(error, stage_ids) or sole_failed
        if target is not None:
            by_stage.setdefault(target, []).append(error)
    return by_stage


def _referenced_stage_id(error: MissionError, stage_ids: set[UUID]) -> UUID | None:
    """Return the stage this error references, if it names one of this mission's stages."""
    for ref in error.references:
        if ref.key != _STAGE_ID_REF_KEY:
            continue
        try:
            stage_id = UUID(ref.value)
        except ValueError:
            return None
        return stage_id if stage_id in stage_ids else None
    return None
