"""MissionStateService: ingest robot execution telemetry and drive the run lifecycle."""

import logging
from datetime import datetime, timezone

from leitstand_backend.application.mission_events import emit_run_lifecycle
from leitstand_backend.application.run_state_view import (
    publish_run_state,
    settle_stage_state,
)
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_state import (
    MissionError,
    MissionStateMessage,
)
from leitstand_backend.domain.model.mission.run_lifecycle import (
    RunTrigger,
    is_executing,
    is_outcome,
    is_terminal,
    trigger_for_report,
    try_next_state,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import (
    StageStateRecord,
)
from leitstand_backend.ports.inbound.mission_state import (
    HandleRobotOfflineCommand,
    MissionStateUseCase,
    RecordMissionStateCommand,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository

logger = logging.getLogger(__name__)


def _live_records(state: MissionStateMessage, stages: list[Stage]) -> list[StageStateRecord]:
    """Map a robot frame's stage states onto runtime records, indexed by the run's own plan.

    ``stage_index`` comes from the stage's position in the run's snapshot, not the frame's
    order, so a partial frame cannot shift it; a reported stage absent from the plan is ignored.
    """
    index_by_id = {stage.stage_id: index for index, stage in enumerate(stages)}
    records: list[StageStateRecord] = []
    for stage_state in state.stage_states:
        index = index_by_id.get(stage_state.stage_id)
        if index is None:
            continue
        records.append(
            StageStateRecord(
                stage_id=stage_state.stage_id,
                header_id=state.header_id,
                stage_index=index,
                status=stage_state.status,
                progress=stage_state.progress,
                started_at=stage_state.started_at,
                ended_at=stage_state.ended_at,
                result=stage_state.result,
                source_ts=state.timestamp,
            )
        )
    return records


class MissionStateService(MissionStateUseCase):
    """Ingest robot execution telemetry and drive the backend run lifecycle.

    The robot is authoritative for execution (stage progress, terminal outcome); the backend owns
    the lifecycle state machine, so each frame becomes a trigger the transition table allows or
    ignores. Frames are ordered by the robot's ``header_id``, dropped when another robot sends
    them, and cannot touch a run that has gone terminal.
    """

    def __init__(self, runs: MissionRunRepository, events: EventPublisher):
        self._runs = runs
        self._events = events

    async def record(self, command: RecordMissionStateCommand) -> None:
        state = command.state
        run = await self._runs.get_for_update(state.run_id)
        if run is None:
            logger.warning(
                "mission_state for unknown run %s from robot %s; dropping",
                state.run_id,
                command.robot_id,
            )
            return
        if run.robot_id != command.robot_id:
            # A robot that accepted late, or is still driving a run the backend gave up on,
            # must not be able to write another robot's run.
            logger.warning(
                "mission_state for run %s from robot %s, which is not its robot %s; dropping",
                state.run_id,
                command.robot_id,
                run.robot_id,
            )
            return

        now = datetime.now(timezone.utc)
        await self._runs.touch_last_frame(run.run_id, now)
        live = _live_records(state, run.stages)

        current_status = run.status
        transition: tuple[RunStatus, RunTrigger] | None = None
        failure_errors: list[MissionError] | None = None
        trigger = trigger_for_report(current_status, state.exec_status)
        target = try_next_state(current_status, trigger) if trigger is not None else None
        if target is None and is_terminal(current_status) and not is_outcome(state.exec_status):
            # The robot is still working a run the backend has closed: the only dropped frame
            # worth a log line.
            logger.warning(
                "robot %s reports run %s as %s, but it is already %s",
                command.robot_id,
                state.run_id,
                state.exec_status.name,
                current_status.value,
            )
        if target is not None and trigger is not None:
            # A failure keeps the dispatch warning next to the robot's errors; any other outcome
            # clears it, since the robot has reported after all.
            failure_errors = (
                [*(run.failure_errors or []), *state.errors]
                if target is RunStatus.FAILED
                else ([] if run.failure_errors else None)
            )
            if (
                await self._runs.update_status(
                    run.run_id, target, expected=current_status, errors=failure_errors
                )
                is not None
            ):
                transition = (target, trigger)
                current_status = target

        if transition is not None and is_terminal(current_status):
            # The arriving frame is the overlay: it and the persisted rows can each hold stages
            # the other lacks, and every stage needs a terminal status.
            await settle_stage_state(
                self._runs, self._events, run, current_status, failure_errors, overlay=live
            )
        elif live and is_executing(current_status):
            # Gate on is_executing so a stray frame for a settled run cannot write stage rows;
            # the held row lock keeps current_status stable.
            await self._runs.upsert_stage_runs(run.run_id, live)
            # Publish the read-back rows, not this frame's records: a frame that lost the
            # header ordering wrote nothing and must not regress the latched view.
            rows = await self._runs.get_stage_runs(run.run_id)
            publish_run_state(self._events, run, rows, None)

        if transition is not None:
            emit_run_lifecycle(
                self._events, run.mission_id, run.run_id, run.robot_id, transition[0], transition[1]
            )

    async def handle_robot_offline(self, command: HandleRobotOfflineCommand) -> None:
        """Record that a robot holding live runs is unreachable, without settling those runs.

        Losing contact does not stop a machine: Nav2 holds the goal and drives on, so ending the
        run here would misreport it and release the robot for another dispatch. The robot's own
        frames settle it when contact returns, or reconciliation does if it comes back silent.
        """
        live = await self._runs.list_active_by_robot(command.robot_id)
        if live:
            logger.warning(
                "robot %s went offline holding %d live run(s): %s",
                command.robot_id,
                len(live),
                ", ".join(str(run.run_id) for run in live),
            )
