"""Settle live runs a reconnected robot turns out not to be executing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
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

# A dispatch may still be in flight when the robot answers, so a PENDING run this young is left
# to the dispatch path.
_DISPATCH_WINDOW = timedelta(seconds=10)


async def reconcile_robot_runs(
    runs: MissionRunRepository,
    dispatcher: MissionDispatcher,
    events: EventPublisher,
    robot_id: str,
    since: datetime,
    claimed_run_id: str | None = None,
    claim_reported: bool = False,
) -> list[UUID]:
    """Close the robot's live runs it does not hold, and return their ids.

    When the robot reported its claim, every live run but the claimed one is closed at once (a
    claim of null closes them all). Without a claim, a run the robot has not reported since
    ``since`` is closed; that path serves clients that do not answer the claim yet. The cancel
    is sent first, because the only harmful mistake is a robot that is still moving.
    """
    ended: list[UUID] = []
    now = datetime.now(since.tzinfo)
    for summary in await runs.list_active_by_robot(robot_id):
        run = await runs.get(summary.run_id)
        if run is None or is_terminal(run.status):
            continue
        if run.created_at >= since:
            # Started after the robot answered again, so this reconnection cannot judge it; the
            # dispatch path settles it.
            continue
        if claim_reported and str(run.run_id) == claimed_run_id:
            continue
        if run.status is RunStatus.PENDING and now - run.created_at < _DISPATCH_WINDOW:
            continue
        if (
            not claim_reported
            and run.last_report is not None
            and run.last_report.received_at >= since
        ):
            continue

        await dispatcher.cancel(run.run_id, run.robot_id)
        error = MissionError(
            origin=ErrorOrigin.BACKEND,
            severity=ErrorSeverity.FATAL,
            type="run_unclaimed_after_reconnect",
            description=_REASON,
        )
        target = RunStatus.CANCELLED if run.status is RunStatus.CANCELLING else RunStatus.FAILED
        updated = await runs.update_status(
            run.run_id,
            target,
            expected=run.status,
            trigger=RunTrigger.RECONCILE,
            detail={"reason": _REASON},
            errors=[error],
        )
        if updated is None:
            continue

        await settle_stage_state(runs, events, run, target, [error])
        emit_run_lifecycle(
            events,
            run.mission_id,
            run.run_id,
            run.robot_id,
            target,
            RunTrigger.RECONCILE,
            reason=_REASON,
        )
        ended.append(run.run_id)

    if ended:
        logger.warning(
            "robot %s reconnected without claiming %d run(s); closed: %s",
            robot_id,
            len(ended),
            ", ".join(str(rid) for rid in ended),
        )
    return ended
