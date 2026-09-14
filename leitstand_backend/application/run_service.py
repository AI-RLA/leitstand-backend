"""RunService: start, steer and annotate runs of a mission."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.application.mission_events import emit_run_lifecycle
from leitstand_backend.application.mission_validation import (
    require_current_boundary,
    validate_against_factsheet,
    validate_plan_fits_robot,
)
from leitstand_backend.application.run_state_view import (
    publish_run_state,
    settle_stage_state,
)
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.errors import (
    AmbiguousRun,
    InvalidMissionTransition,
    MissionArchived,
    MissionNotFoundError,
    MissionRunInProgress,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RunNotFoundError,
    UnknownSite,
)
from leitstand_backend.domain.model.mission.mission import Stage, referenced_site_ids
from leitstand_backend.domain.model.mission.mission_run import (
    MissionRun,
    RunSiteAnchor,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.run_lifecycle import (
    RunTrigger,
    is_active,
    next_state,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import (
    waiting_stage_statuses,
)
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.ports.inbound.run_management import (
    AnnotateRunCommand,
    CancelRunCommand,
    DeleteRunCommand,
    DispatchOutcome,
    PauseRunCommand,
    ResumeRunCommand,
    RunManagementUseCase,
    SettleRunCommand,
    StartRunCommand,
)
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from leitstand_backend.ports.outbound.site_repository import SiteRepository


class RunService(RunManagementUseCase):
    def __init__(
        self,
        missions: MissionRepository,
        runs: MissionRunRepository,
        dispatcher: MissionDispatcher,
        factsheets: RobotFactsheetView,
        fields: FieldRepository,
        sites: SiteRepository,
        events: EventPublisher,
        audit: AuditWriter,
    ):
        self._missions = missions
        self._runs = runs
        self._dispatcher = dispatcher
        self._factsheets = factsheets
        self._fields = fields
        self._sites = sites
        self._events = events
        self._audit = audit

    async def _freeze_site_anchors(self, stages: list[Stage]) -> dict[str, RunSiteAnchor] | None:
        """Copy the frame of every site this plan drives in, or None when it drives in none.

        A site-local waypoint means nothing without its anchor, and a site can be moved or
        deleted long after the run that used it. Copying the frame is what keeps the run's own
        geometry readable, the same reason the plan itself is copied.
        """
        anchors: dict[str, RunSiteAnchor] = {}
        for site_id in referenced_site_ids(stages):
            site = await self._sites.get(site_id)
            if site is None:
                raise UnknownSite(site_id)
            anchors[str(site_id)] = RunSiteAnchor(
                name=site.name,
                anchor_lat=site.anchor_lat,
                anchor_lon=site.anchor_lon,
                anchor_heading_deg=site.anchor_heading_deg,
            )
        return anchors or None

    async def prepare(self, command: StartRunCommand) -> MissionRun:
        """Validate and record the run as PENDING; the robot is not contacted here.

        Runs under the mission's row lock, which serialises two dispatches of one mission and
        so makes the default refusal of a concurrent run race-free without an index. The robot
        index in the run store is what refuses a second run on one robot.
        """
        mission = await self._missions.get_for_update(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)
        if mission.archived_at is not None:
            raise MissionArchived(command.mission_id)

        robot_id = command.robot_id or mission.assigned_robot_id
        if robot_id is None:
            raise NoRobotAssigned(command.mission_id)

        holders = await self._runs.list_active_by_robot(robot_id)
        if holders:
            raise RobotBusy(robot_id, holders[0].mission_id)
        active = await self._runs.list_active_by_mission(command.mission_id)
        if active and not command.allow_concurrent:
            raise MissionRunInProgress(command.mission_id, [run.run_id for run in active])

        factsheet = self._factsheets.latest(robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(robot_id)
        validate_against_factsheet(mission, robot_id, factsheet)
        validate_plan_fits_robot(robot_id, mission.stages, factsheet)
        await require_current_boundary(self._fields, mission.stages)

        now = datetime.now(timezone.utc)
        run = await self._runs.create(
            MissionRun(
                run_id=uuid4(),
                mission_id=mission.mission_id,
                robot_id=robot_id,
                status=RunStatus.PENDING,
                stages=mission.stages,
                stages_digest=stages_digest(mission.stages),
                site_anchors=await self._freeze_site_anchors(mission.stages),
                origin=command.origin,
                notes=command.notes,
                created_at=now,
                updated_at=now,
            )
        )
        # Replace whatever the mission's latched state frame held (the previous run's terminal
        # view) with this run's all-WAITING baseline, so a subscriber never sees another run's
        # stages under this run's id.
        publish_run_state(self._events, run, waiting_stage_statuses(run.stages, now), None)
        await self._audit(
            "mission.dispatch",
            "mission",
            str(mission.mission_id),
            {
                "run_id": str(run.run_id),
                "robot_id": robot_id,
                "allow_concurrent": command.allow_concurrent,
            },
        )
        return run

    async def settle(self, command: SettleRunCommand) -> MissionRun:
        """Record the robot's answer to the dispatch.

        The compare-and-set from PENDING may fail: the robot's first frame or a cancel may have
        moved the run on while the reply was in flight. Each case is resolved from the run's
        current status.
        """
        run = await self._runs.get_for_update(command.run_id)
        if run is None:
            raise RunNotFoundError(command.run_id)

        if command.outcome is DispatchOutcome.ACCEPTED:
            updated = await self._runs.update_status(
                run.run_id,
                RunStatus.DISPATCHED,
                expected=RunStatus.PENDING,
                dispatched_at=command.replied_at,
            )
            if updated is not None and updated.status is RunStatus.DISPATCHED:
                emit_run_lifecycle(
                    self._events,
                    run.mission_id,
                    run.run_id,
                    run.robot_id,
                    RunStatus.DISPATCHED,
                    RunTrigger.ACCEPT,
                )
                return updated
            # A frame or a cancel arrived before the reply; the robot has the goal either way.
            await self._runs.mark_dispatched_at(run.run_id, command.replied_at)
            current = await self._runs.get(run.run_id) or run
            if current.status is RunStatus.CANCELLED:
                await self._dispatcher.cancel(run.run_id, run.robot_id)
            return current

        if command.outcome is not DispatchOutcome.REJECTED:
            # No reply is not a refusal: the executor starts before it answers, so the run stays
            # live until its own frames or reconciliation settle it.
            error_type, description = (
                ("dispatch_error", command.reason or "The dispatch could not be sent.")
                if command.outcome is DispatchOutcome.ERROR
                else (
                    "dispatch_timeout",
                    "The robot did not acknowledge the dispatch in time.",
                )
            )
            if run.status is RunStatus.PENDING:
                unsettled = await self._runs.update_status(
                    run.run_id,
                    RunStatus.PENDING,
                    expected=RunStatus.PENDING,
                    errors=[
                        MissionError(
                            origin=ErrorOrigin.BACKEND,
                            severity=ErrorSeverity.WARNING,
                            type=error_type,
                            description=description,
                        )
                    ],
                )
                await self._audit(
                    "mission.dispatch_unconfirmed",
                    "mission",
                    str(run.mission_id),
                    {
                        "run_id": str(run.run_id),
                        "robot_id": run.robot_id,
                        "error_type": error_type,
                        "reason": description,
                    },
                )
                return unsettled or run
            # The run moved on while the reply was in flight, so the robot has the goal; the frame
            # path never writes a dispatch time, and a cancel that landed meanwhile must still
            # reach the robot.
            await self._runs.mark_dispatched_at(run.run_id, command.replied_at)
            if run.status is RunStatus.CANCELLED:
                await self._dispatcher.cancel(run.run_id, run.robot_id)
            return await self._runs.get(run.run_id) or run

        error_type, description = (
            "dispatch_rejected",
            command.reason or "The robot rejected the dispatch.",
        )
        target, trigger = RunStatus.REJECTED, RunTrigger.REJECT
        error = MissionError(
            origin=ErrorOrigin.BACKEND,
            severity=ErrorSeverity.FATAL,
            type=error_type,
            description=description,
        )
        if run.status is RunStatus.PENDING:
            updated = await self._runs.update_status(
                run.run_id, target, expected=RunStatus.PENDING, errors=[error]
            )
            if updated is not None:
                await self._finalize(updated, [error])
                emit_run_lifecycle(
                    self._events,
                    run.mission_id,
                    run.run_id,
                    run.robot_id,
                    target,
                    trigger,
                    reason=description,
                )
                await self._audit(
                    "mission.dispatch_failed",
                    "mission",
                    str(run.mission_id),
                    {
                        "run_id": str(run.run_id),
                        "robot_id": run.robot_id,
                        "error_type": error_type,
                        "reason": description,
                    },
                )
                return updated
        # Only a rejection reaches here, and only when the run moved on before it settled: the
        # robot never took the goal, so the run's current status stands.
        return await self._runs.get(run.run_id) or run

    async def cancel(self, command: CancelRunCommand) -> MissionRun:
        run = await self._target(command.mission_id, command.run_id)
        target = next_state(run.status, RunTrigger.CANCEL)
        updated = await self._runs.update_status(run.run_id, target, expected=run.status)
        if updated is None:
            return run
        # Sent even while PENDING: a cancel for a run the robot is not executing has no effect,
        # and settle re-sends if the goal landed after all.
        await self._finalize(updated, None)
        await self._dispatcher.cancel(run.run_id, run.robot_id, command.mode)
        emit_run_lifecycle(
            self._events, run.mission_id, run.run_id, run.robot_id, target, RunTrigger.CANCEL
        )
        await self._audit(
            "mission.cancel", "mission", str(run.mission_id), {"run_id": str(run.run_id)}
        )
        return await self._runs.get(run.run_id) or updated

    async def pause(self, command: PauseRunCommand) -> MissionRun:
        return await self._steer(
            command.mission_id, command.run_id, RunTrigger.PAUSE, "pause", "mission.pause"
        )

    async def resume(self, command: ResumeRunCommand) -> MissionRun:
        return await self._steer(
            command.mission_id, command.run_id, RunTrigger.RESUME, "resume", "mission.resume"
        )

    async def annotate(self, command: AnnotateRunCommand) -> MissionRun:
        updated = await self._runs.set_notes(command.run_id, command.notes)
        if updated is None:
            raise RunNotFoundError(command.run_id)
        await self._audit(
            "run.annotate", "mission", str(updated.mission_id), {"run_id": str(updated.run_id)}
        )
        return updated

    async def delete(self, command: DeleteRunCommand) -> None:
        run = await self._runs.get_for_update(command.run_id)
        if run is None:
            raise RunNotFoundError(command.run_id)
        if is_active(run.status):
            # A driving robot's frames could not be recorded, and the robot would look free.
            raise InvalidMissionTransition(run.status, "delete")
        await self._runs.delete(run.run_id)
        if not await self._runs.count_by_mission(run.mission_id):
            # Nothing is left to describe, and the latched frame would otherwise show a finished
            # run on a mission that reads as never run.
            self._events.unlatch(event_topics.mission_topic(run.mission_id, "state"))
        # The row goes; the audit log keeps the attempt.
        await self._audit(
            "run.delete",
            "mission",
            str(run.mission_id),
            {"run_id": str(run.run_id), "status": run.status.value, "robot_id": run.robot_id},
        )

    async def _steer(
        self,
        mission_id: UUID,
        run_id: UUID | None,
        trigger: RunTrigger,
        dispatcher_action: str,
        audit_action: str,
    ) -> MissionRun:
        run = await self._target(mission_id, run_id)
        target = next_state(run.status, trigger)
        updated = await self._runs.update_status(run.run_id, target, expected=run.status)
        if updated is None:
            return run
        await getattr(self._dispatcher, dispatcher_action)(run.run_id, run.robot_id)
        emit_run_lifecycle(self._events, run.mission_id, run.run_id, run.robot_id, target, trigger)
        await self._audit(audit_action, "mission", str(run.mission_id), {"run_id": str(run.run_id)})
        return updated

    async def _target(self, mission_id: UUID, run_id: UUID | None) -> MissionRun:
        """Resolve which run a mission-addressed command means, under the mission's lock.

        With one active run the id is optional; with several it is required; a named run must
        be this mission's and still active.
        """
        if await self._missions.get_for_update(mission_id) is None:
            raise MissionNotFoundError(mission_id)
        if run_id is not None:
            run = await self._runs.get_for_update(run_id)
            if run is None or run.mission_id != mission_id:
                raise RunNotFoundError(run_id)
            if not is_active(run.status):
                raise InvalidMissionTransition(run.status, "steer")
            return run
        active = await self._runs.list_active_by_mission(mission_id)
        if len(active) > 1:
            raise AmbiguousRun(mission_id, [run.run_id for run in active])
        if not active:
            latest = await self._runs.latest_by_mission([mission_id])
            summary = latest.get(mission_id)
            if summary is None:
                raise RunNotFoundError(mission_id, mission_has_none=True)
            raise InvalidMissionTransition(summary.status, "steer")
        run = await self._runs.get_for_update(active[0].run_id)
        if run is None:
            raise RunNotFoundError(active[0].run_id)
        return run

    async def _finalize(self, run: MissionRun, failure_errors: list[MissionError] | None) -> None:
        await settle_stage_state(self._runs, self._events, run, run.status, failure_errors)
