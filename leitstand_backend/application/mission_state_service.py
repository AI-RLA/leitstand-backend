"""MissionStateService - ingest robot execution telemetry and drive the lifecycle."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from leitstand_backend.application.mission_events import emit_mission_lifecycle
from leitstand_backend.application.mission_state_view import build_mission_state_view
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.mission import MissionStatus, Stage
from leitstand_backend.domain.model.mission.mission_lifecycle import (
    MissionTrigger,
    is_executing,
    is_terminal,
    next_state,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
    MissionExecStatus,
    MissionStateMessage,
)
from leitstand_backend.domain.model.mission.stage_state_record import (
    StageStateRecord,
    final_stage_statuses,
)
from leitstand_backend.ports.inbound.mission_state import (
    HandleRobotOfflineCommand,
    MissionStateUseCase,
    RecordMissionStateCommand,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.mission_repository import MissionRepository

logger = logging.getLogger(__name__)


def _telemetry_trigger(
    current: MissionStatus,
    exec_status: MissionExecStatus,
) -> MissionTrigger | None:
    """Map a robot execution-status frame onto a lifecycle trigger, or None.

    The robot is authoritative for execution truth (running/paused, and how the run
    ended); the backend owns the lifecycle. A trigger is returned only when it yields
    a legal transition from ``current``; otherwise the frame is confirmation or a
    no-op (e.g. a repeated RUNNING frame while already RUNNING).
    """
    if exec_status is MissionExecStatus.SUCCEEDED:
        # Accept a terminal SUCCEEDED even from DISPATCHED: if the RUNNING/ACK frame was
        # lost, the latched terminal frame (the one most likely to survive a subscriber
        # gap) must still complete the mission rather than strand it at DISPATCHED.
        if current in (MissionStatus.RUNNING, MissionStatus.DISPATCHED):
            return MissionTrigger.COMPLETE
        return None

    if exec_status is MissionExecStatus.FAILED:
        if current in (MissionStatus.RUNNING, MissionStatus.PAUSED):
            return MissionTrigger.FAIL
        if current is MissionStatus.DISPATCHED:
            return MissionTrigger.REJECT
        return None

    if exec_status is MissionExecStatus.CANCELLED:
        if current in (
            MissionStatus.DISPATCHED,
            MissionStatus.RUNNING,
            MissionStatus.PAUSED,
        ):
            return MissionTrigger.CANCEL
        return None

    if exec_status is MissionExecStatus.RUNNING:
        if current is MissionStatus.DISPATCHED:
            return MissionTrigger.ACK
        if current is MissionStatus.PAUSED:
            return MissionTrigger.RESUME
        return None

    if exec_status is MissionExecStatus.PAUSED and current is MissionStatus.RUNNING:
        return MissionTrigger.PAUSE

    return None


def _live_records(state: MissionStateMessage, stages: list[Stage]) -> list[StageStateRecord]:
    """Map a robot frame's stage states onto runtime records, indexed by the definition.

    ``stage_index`` comes from the stage's position in the mission definition, not the
    frame's order, so a partial frame cannot shift it; a reported stage absent from the
    definition is ignored.
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
    """Ingests robot execution telemetry and drives the backend mission lifecycle.

    The robot is authoritative for *execution* (stage progress, terminal outcome); the
    backend owns the *lifecycle* state machine. Each frame is translated into a lifecycle
    trigger via ``next_state`` and its stage states are upserted into the clean per-stage
    store: an older frame never overwrites a newer one (ordered by the robot's ``header_id``
    counter), and a frame arriving after the mission went terminal cannot clobber the
    resolved rows (the live upsert applies only while the mission is executing).
    """

    def __init__(self, repo: MissionRepository, events: EventPublisher):
        self._repo = repo
        self._events = events

    async def record(self, command: RecordMissionStateCommand) -> None:
        state = command.state
        mission_record = await self._repo.get_record_for_update(state.mission_id)
        if mission_record is None:
            logger.warning(
                "mission_state for unknown mission %s from robot %s; dropping",
                state.mission_id,
                command.robot_id,
            )
            return

        stages = mission_record.mission.stages
        live = _live_records(state, stages)

        current_status = mission_record.status
        transition: tuple[MissionStatus, MissionTrigger] | None = None
        failure_errors: list[MissionError] | None = None
        trigger = _telemetry_trigger(current_status, state.exec_status)
        if trigger is not None:
            try:
                target = next_state(current_status, trigger)
            except InvalidMissionTransition:
                logger.warning(
                    "dropping illegal telemetry transition %s --%s--> for mission %s",
                    current_status.value,
                    trigger,
                    state.mission_id,
                )
            else:
                # On a robot-reported failure, persist the robot's structured errors as the
                # durable cause in the same transaction as the status.
                failure_errors = state.errors if target is MissionStatus.FAILED else None
                if (
                    await self._repo.update_status(state.mission_id, target, errors=failure_errors)
                    is not None
                ):
                    transition = (target, trigger)
                    current_status = target

        now = datetime.now(timezone.utc)
        if transition is not None and is_terminal(current_status):
            # This final frame and the persisted rows can each hold stages the other lacks,
            # so resolve over the union to give every stage its terminal status.
            live_by_id = {
                rec.stage_id: rec for rec in await self._repo.get_stage_states(state.mission_id)
            }
            live_by_id.update({rec.stage_id: rec for rec in live})
            resolved = final_stage_statuses(stages, live_by_id, current_status, now)
            await self._repo.overwrite_stage_states(state.mission_id, resolved)
            self._publish_state(state.mission_id, resolved, failure_errors)
        elif live and is_executing(current_status):
            # Gate on is_executing so a stray frame for a settled or not-yet-dispatched
            # mission cannot write stage rows; the held row lock keeps current_status stable.
            await self._repo.upsert_stage_states(state.mission_id, live)
            # Publish the read-back rows, not this frame's records: a frame that lost the
            # header ordering wrote nothing and must not regress the latched view.
            rows = await self._repo.get_stage_states(state.mission_id)
            self._publish_state(state.mission_id, rows, None)

        if transition is not None:
            emit_mission_lifecycle(self._events, state.mission_id, transition[0], transition[1])

    def _publish_state(
        self,
        mission_id: UUID,
        records: list[StageStateRecord],
        failure_errors: list[MissionError] | None,
    ) -> None:
        view = build_mission_state_view(mission_id, records, failure_errors)
        self._events.publish(
            event_topics.mission_topic(mission_id, "state"),
            view.model_dump(mode="json"),
            latch=True,
        )

    async def handle_robot_offline(self, command: HandleRobotOfflineCommand) -> None:
        # Fail only what the robot was executing; an ASSIGNED mission is excluded because no
        # goal was sent and FAIL is not a legal transition from ASSIGNED.
        executing = await self._repo.list_executing_by_robot(command.robot_id)
        now = datetime.now(timezone.utc)
        for mission in executing:
            reason = "robot offline mid-mission"
            error = MissionError(
                origin=ErrorOrigin.BACKEND,
                severity=ErrorSeverity.FATAL,
                type="robot_offline_mid_mission",
                description=reason,
            )
            updated = await self._repo.update_status(
                mission.mission_id, MissionStatus.FAILED, errors=[error]
            )
            if updated is None:
                continue
            live_by_id = {
                rec.stage_id: rec for rec in await self._repo.get_stage_states(mission.mission_id)
            }
            resolved = final_stage_statuses(mission.stages, live_by_id, MissionStatus.FAILED, now)
            await self._repo.overwrite_stage_states(mission.mission_id, resolved)
            self._publish_state(mission.mission_id, resolved, [error])
            emit_mission_lifecycle(
                self._events,
                mission.mission_id,
                MissionStatus.FAILED,
                MissionTrigger.FAIL,
                reason=reason,
            )
