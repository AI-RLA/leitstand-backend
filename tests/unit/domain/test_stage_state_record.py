"""Unit tests for the per-stage terminal projection and error attribution."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.domain.model.mission.mission import MissionStatus, NavigationStage
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorReference,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.stage_state_record import (
    StageStateRecord,
    attribute_errors,
    final_stage_statuses,
)
from leitstand_backend.domain.model.mission.stage_status import StageStatus as S
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint

_T0 = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
_RESOLVE_AT = datetime(2026, 5, 27, 13, 0, 0, tzinfo=timezone.utc)


def _stages(count: int) -> list[NavigationStage]:
    return [
        NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.0, lon=8.0)])
        for _ in range(count)
    ]


def _record(stage_id: UUID, index: int, status: S, *, started: bool = False) -> StageStateRecord:
    return StageStateRecord(
        stage_id=stage_id,
        stage_index=index,
        status=status,
        started_at=_T0 if started else None,
        source_ts=_T0,
    )


@pytest.mark.parametrize(
    ("live, mission_status, expected"),
    [
        (
            [S.FINISHED, S.FAILED, S.WAITING],
            MissionStatus.CANCELLED,
            [S.FINISHED, S.FAILED, S.SKIPPED],
        ),
        (
            [S.FINISHED, S.RUNNING, S.WAITING],
            MissionStatus.CANCELLED,
            [S.FINISHED, S.CANCELLED, S.SKIPPED],
        ),
        (
            [S.FINISHED, S.RUNNING, S.WAITING],
            MissionStatus.FAILED,
            [S.FINISHED, S.FAILED, S.SKIPPED],
        ),
        (
            [S.WAITING, S.RUNNING, S.FINISHED],
            MissionStatus.SUCCEEDED,
            [S.FINISHED, S.FINISHED, S.FINISHED],
        ),
        (
            [S.FINISHED, S.WAITING, S.WAITING],
            MissionStatus.CANCELLED,
            [S.FINISHED, S.SKIPPED, S.SKIPPED],
        ),
        ([S.FINISHED, S.FAILED, S.FAILED], MissionStatus.FAILED, [S.FINISHED, S.FAILED, S.FAILED]),
        ([None, None], MissionStatus.FAILED, [S.SKIPPED, S.SKIPPED]),
        ([S.FINISHED, None, None], MissionStatus.CANCELLED, [S.FINISHED, S.SKIPPED, S.SKIPPED]),
    ],
)
def test_final_stage_statuses(
    live: list[S | None], mission_status: MissionStatus, expected: list[S]
) -> None:
    stages = _stages(len(live))
    live_by_id = {
        stages[i].stage_id: _record(stages[i].stage_id, i, status)
        for i, status in enumerate(live)
        if status is not None
    }
    resolved = final_stage_statuses(stages, live_by_id, mission_status, _RESOLVE_AT)
    assert [r.status for r in resolved] == expected
    assert [r.stage_index for r in resolved] == list(range(len(live)))


def test_ended_at_stamped_only_for_started_terminal_stages() -> None:
    stages = _stages(2)
    # Stage 0 was running and is cancelled; stage 1 never reported and is skipped.
    live_by_id = {stages[0].stage_id: _record(stages[0].stage_id, 0, S.RUNNING, started=True)}

    resolved = final_stage_statuses(stages, live_by_id, MissionStatus.CANCELLED, _RESOLVE_AT)
    by_id = {r.stage_id: r for r in resolved}

    assert by_id[stages[0].stage_id].status is S.CANCELLED
    assert by_id[stages[0].stage_id].ended_at == _RESOLVE_AT
    assert by_id[stages[1].stage_id].status is S.SKIPPED
    assert by_id[stages[1].stage_id].ended_at is None


def _error(stage_id: UUID | None = None) -> MissionError:
    references = [ErrorReference(key="stage_id", value=str(stage_id))] if stage_id else []
    return MissionError(
        origin=ErrorOrigin.ROBOT,
        severity=ErrorSeverity.FATAL,
        type="nav2_unavailable",
        description="nav2 did not appear",
        references=references,
    )


def test_attribute_errors_by_stage_reference() -> None:
    stages = _stages(2)
    resolved = [
        _record(stages[0].stage_id, 0, S.FINISHED),
        _record(stages[1].stage_id, 1, S.FAILED),
    ]
    error = _error(stage_id=stages[0].stage_id)
    assert attribute_errors([error], resolved) == {stages[0].stage_id: [error]}


def test_attribute_errors_falls_back_to_sole_failed_stage() -> None:
    stages = _stages(2)
    resolved = [
        _record(stages[0].stage_id, 0, S.FINISHED),
        _record(stages[1].stage_id, 1, S.FAILED),
    ]
    error = _error()
    assert attribute_errors([error], resolved) == {stages[1].stage_id: [error]}


def test_attribute_errors_ambiguous_multi_failure_stays_mission_level() -> None:
    stages = _stages(2)
    resolved = [
        _record(stages[0].stage_id, 0, S.FAILED),
        _record(stages[1].stage_id, 1, S.FAILED),
    ]
    assert attribute_errors([_error()], resolved) == {}


def test_attribute_errors_none() -> None:
    assert attribute_errors(None, []) == {}
