"""Settle live runs a reconnected robot turns out not to be executing."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from leitstand_backend.application.mission_events import emit_run_lifecycle
from leitstand_backend.application.run_state_view import settle_stage_state
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger, is_terminal
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository

logger = logging.getLogger(__name__)

_REASON = "the robot reconnected without reporting this run"


async def reconcile_robot_runs(
    runs: MissionRunRepository,
    dispatcher: MissionDispatcher,
    events: EventPublisher,
    robot_id: str,
    since: datetime,
) -> list[UUID]:
    """Fail the robot's live runs it has not reported since ``since``, and return their ids.

    A robot executing a run publishes its state every few seconds, so one reachable for the
    grace window without mentioning a run it held is not driving it. The cancel is sent first,
    because the only harmful mistake is a robot that is still moving.
    """
    ended: list[UUID] = []
    for summary in await runs.list_active_by_robot(robot_id):
        run = await runs.get(summary.run_id)
        if run is None or is_terminal(run.status):
            continue
        if run.created_at >= since:
            # Started after the robot answered again, so this reconnection cannot judge it; the
            # dispatch path settles it.
            continue
        if run.last_frame_at is not None and run.last_frame_at >= since:
            continue

        await dispatcher.cancel(run.run_id, run.robot_id)
        error = MissionError(
            origin=ErrorOrigin.BACKEND,
            severity=ErrorSeverity.FATAL,
            type="run_unclaimed_after_reconnect",
            description=_REASON,
        )
        updated = await runs.update_status(
            run.run_id, RunStatus.FAILED, expected=run.status, errors=[error]
        )
        if updated is None:
            continue

        await settle_stage_state(runs, events, run, RunStatus.FAILED, [error])
        emit_run_lifecycle(
            events,
            run.mission_id,
            run.run_id,
            run.robot_id,
            RunStatus.FAILED,
            RunTrigger.TIMEOUT,
            reason=_REASON,
        )
        ended.append(run.run_id)

    if ended:
        logger.warning(
            "robot %s reconnected without claiming %d run(s); failed: %s",
            robot_id,
            len(ended),
            ", ".join(str(rid) for rid in ended),
        )
    return ended
