"""Per-stage runtime state: the persisted record, the terminal projection, error attribution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import MissionStatus, Stage
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.domain.model.mission.stage_status import StageStatus

_STAGE_ID_REF_KEY = "stage_id"


class StageStateRecord(BaseModel):
    """One mission stage's current runtime state, persisted per ``(mission_id, stage_id)``.

    Distinct from the stage *definition* (waypoints/kind, on the mission): this is the
    *runtime* view. ``header_id`` is the robot's monotone per-frame counter and orders
    concurrent writes so an older or reordered frame never overwrites a newer one;
    ``source_ts`` is the frame's robot-clock time, kept as informational metadata only.
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


def _project_status(status: StageStatus, mission_status: MissionStatus) -> StageStatus:
    """Project one stage's live status onto its final status given the mission outcome."""
    if mission_status is MissionStatus.SUCCEEDED:
        return StageStatus.FINISHED
    # The mission ended on FAILED or CANCELLED.
    if status is StageStatus.FINISHED:
        return StageStatus.FINISHED
    if status is StageStatus.FAILED:
        return StageStatus.FAILED
    if status is StageStatus.WAITING:
        return StageStatus.SKIPPED
    # An in-flight stage (INITIALIZING / RUNNING / PAUSED) inherits the mission's reason:
    # a commanded cancel makes it CANCELLED, anything else a genuine failure.
    return (
        StageStatus.CANCELLED if mission_status is MissionStatus.CANCELLED else StageStatus.FAILED
    )


def final_stage_statuses(
    stages: list[Stage],
    live_by_id: dict[UUID, StageStateRecord],
    mission_status: MissionStatus,
    now: datetime,
) -> list[StageStateRecord]:
    """Resolve every defined stage's final status when a mission reaches a terminal state.

    The stage definitions are the authoritative spine: each is matched to its live
    record by ``stage_id`` (or defaulted to WAITING when none was reported) and
    projected via :func:`_project_status`, so the result is correct regardless of how
    much a producer reported. A stage that started and reached a terminal end records
    when it stopped; a skipped or never-started stage keeps ``ended_at = None`` and so
    carries no spurious duration.
    """
    resolved: list[StageStateRecord] = []
    for index, stage in enumerate(stages):
        live = live_by_id.get(stage.stage_id)
        current = live.status if live is not None else StageStatus.WAITING
        final = _project_status(current, mission_status)
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
            )
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
