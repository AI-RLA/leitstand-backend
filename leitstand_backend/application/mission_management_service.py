"""MissionManagementService - operator CRUD + dispatch + lifecycle for missions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.application.mission_events import emit_mission_lifecycle
from leitstand_backend.application.mission_state_view import build_mission_state_view
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.errors import (
    GeneratedPlanNotEditable,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionDispatchTimeout,
    MissionNotFoundError,
    MissionRejectedByRobot,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    StageNotHomogeneous,
    StaleCoverageBoundary,
    UnknownSite,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.coverage import CoverageProvenance, boundary_digest
from leitstand_backend.domain.model.mission.mission import (
    CoverageStage,
    Mission,
    MissionStatus,
    NavigationStage,
    Segment,
    Stage,
    StageKind,
    referenced_site_ids,
    stage_waypoints,
)
from leitstand_backend.domain.model.mission.mission_lifecycle import (
    MissionTrigger,
    is_terminal,
    next_state,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.stage_state_record import final_stage_statuses
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet, WaypointKind
from leitstand_backend.ports.inbound.mission_management import (
    AssignMissionCommand,
    CancelMissionCommand,
    CoverageStageInput,
    CreateMissionCommand,
    DeleteMissionCommand,
    DispatchMissionCommand,
    MissionManagementUseCase,
    PauseMissionCommand,
    ResetMissionCommand,
    ResumeMissionCommand,
    StageInput,
    UnassignMissionCommand,
    UpdateMissionCommand,
)
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from leitstand_backend.ports.outbound.site_repository import SiteRepository


class MissionManagementService(MissionManagementUseCase):
    def __init__(
        self,
        repo: MissionRepository,
        dispatcher: MissionDispatcher,
        factsheets: RobotFactsheetView,
        events: EventPublisher,
        audit: AuditWriter,
        sites: SiteRepository,
        fields: FieldRepository,
    ):
        self._repo = repo
        self._dispatcher = dispatcher
        self._factsheets = factsheets
        self._events = events
        self._audit = audit
        self._sites = sites
        self._fields = fields

    async def create(self, command: CreateMissionCommand) -> Mission:
        _validate_homogeneity(command.stages)
        await self._validate_sites_exist(command.stages)

        now = datetime.now(timezone.utc)
        mission = Mission(
            mission_id=uuid4(),
            name=command.name,
            description=command.description,
            stages=_with_ids(command.stages),
            created_at=now,
            updated_at=now,
        )
        saved = await self._repo.save(mission)
        await self._audit(
            "mission.create",
            "mission",
            str(saved.mission_id),
            command.model_dump(mode="json"),
        )
        # Not a lifecycle event: a mission comes into existence rather than transitioning into it.
        # Announced anyway, or a mission created in one place stays invisible everywhere else.
        self._events.publish(
            event_topics.mission_topic(saved.mission_id, "created"),
            {"mission_id": str(saved.mission_id)},
        )
        return saved

    async def update(self, command: UpdateMissionCommand) -> Mission:
        # Lock the row so a concurrent dispatch cannot flip DRAFT->DISPATCHED between the
        # check and the save, which would upsert new stages onto an executing mission.
        record = await self._repo.get_record_for_update(command.mission_id)
        if record is None:
            raise MissionNotFoundError(command.mission_id)
        before = record.mission
        if record.status is not MissionStatus.DRAFT:
            raise InvalidMissionTransition(record.status, "update")
        # The provenance describes these stages; editing them would leave it describing a path
        # that is gone.
        if command.stages is not None and record.coverage is not None:
            raise GeneratedPlanNotEditable(command.mission_id)

        patch = {
            "name": command.name if command.name is not None else before.name,
            "description": (
                command.description if command.description is not None else before.description
            ),
            "stages": (_with_ids(command.stages) if command.stages is not None else before.stages),
            "updated_at": datetime.now(timezone.utc),
        }
        _validate_homogeneity(patch["stages"])
        await self._validate_sites_exist(patch["stages"])

        updated = before.model_copy(update=patch)
        saved = await self._repo.save(updated)
        await self._audit(
            "mission.update",
            "mission",
            str(command.mission_id),
            {
                "before": before.model_dump(mode="json"),
                "patch": command.model_dump(mode="json", exclude_none=True),
            },
        )
        return saved

    async def assign(self, command: AssignMissionCommand) -> Mission:
        record = await self._repo.get_record_for_update(command.mission_id)
        if record is None:
            raise MissionNotFoundError(command.mission_id)
        mission = record.mission
        current = record.status
        target = next_state(current, MissionTrigger.ASSIGN)

        active = await self._repo.list_active_by_robot(command.robot_id)
        for other in active:
            if other.mission_id != command.mission_id:
                raise RobotBusy(command.robot_id, other.mission_id)

        factsheet = self._factsheets.latest(command.robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(command.robot_id)
        _validate_against_factsheet(mission, command.robot_id, factsheet)
        _validate_plan_fits_robot(command.robot_id, record.coverage, factsheet)
        await self._require_current_boundary(record.coverage)

        if await self._repo.update_status(command.mission_id, target) is None:
            raise InvalidMissionTransition(current, "assign")
        await self._repo.set_robot(command.mission_id, command.robot_id)
        emit_mission_lifecycle(self._events, command.mission_id, target, MissionTrigger.ASSIGN)
        await self._audit(
            "mission.assign",
            "mission",
            str(command.mission_id),
            {"robot_id": command.robot_id},
        )
        return (await self._repo.get(command.mission_id)) or mission

    async def unassign(self, command: UnassignMissionCommand) -> Mission:
        record = await self._repo.get_record_for_update(command.mission_id)
        if record is None:
            raise MissionNotFoundError(command.mission_id)
        mission = record.mission
        current = record.status
        target = next_state(current, MissionTrigger.UNASSIGN)

        if await self._repo.update_status(command.mission_id, target) is None:
            raise InvalidMissionTransition(current, "unassign")
        await self._repo.set_robot(command.mission_id, None)
        emit_mission_lifecycle(self._events, command.mission_id, target, MissionTrigger.UNASSIGN)
        await self._audit("mission.unassign", "mission", str(command.mission_id), None)
        return (await self._repo.get(command.mission_id)) or mission

    async def dispatch(self, command: DispatchMissionCommand) -> Mission:
        record = await self._repo.get_record_for_update(command.mission_id)
        if record is None:
            raise MissionNotFoundError(command.mission_id)
        mission = record.mission
        current = record.status
        target = next_state(current, MissionTrigger.DISPATCH)

        robot_id = command.robot_id or await self._repo.get_assigned_robot(command.mission_id)
        if robot_id is None:
            raise NoRobotAssigned(command.mission_id)

        active = await self._repo.list_active_by_robot(robot_id)
        for other in active:
            if other.mission_id != command.mission_id:
                raise RobotBusy(robot_id, other.mission_id)

        factsheet = self._factsheets.latest(robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(robot_id)
        _validate_against_factsheet(mission, robot_id, factsheet)
        _validate_plan_fits_robot(robot_id, record.coverage, factsheet)
        await self._require_current_boundary(record.coverage)

        if await self._repo.update_status(command.mission_id, target) is None:
            raise InvalidMissionTransition(current, "dispatch")
        dispatched_at = datetime.now(timezone.utc)
        await self._repo.assign_robot(command.mission_id, robot_id, dispatched_at)

        # On dispatch failure (robot reject / timeout) mark the mission FAILED and
        # re-raise. The REST dispatch path runs this inside a dedicated transaction
        # (the dispatch orchestrator in infrastructure) that commits the FAILED write
        # before the error propagates to the route (422 / 504), so the failure is
        # durable rather than rolled back with the request.
        try:
            await self._dispatcher.dispatch(mission, robot_id)
        except (MissionRejectedByRobot, MissionDispatchTimeout) as exc:
            # Use the substance only: the operator views this on the mission's own
            # page, so the str(exc) envelope (robot id + mission id prefix) is noise.
            if isinstance(exc, MissionDispatchTimeout):
                error_type = "dispatch_timeout"
                description = "The robot did not acknowledge the dispatch in time."
            else:
                error_type = "dispatch_rejected"
                description = exc.reason or "The robot rejected the dispatch."
            error = MissionError(
                origin=ErrorOrigin.BACKEND,
                severity=ErrorSeverity.FATAL,
                type=error_type,
                description=description,
            )
            await self._repo.update_status(command.mission_id, MissionStatus.FAILED, errors=[error])
            await self._finalize_stage_state(
                command.mission_id, mission, MissionStatus.FAILED, [error]
            )
            emit_mission_lifecycle(
                self._events,
                command.mission_id,
                MissionStatus.FAILED,
                MissionTrigger.REJECT,
                reason=error.description,
            )
            await self._audit(
                "mission.dispatch_failed",
                "mission",
                str(command.mission_id),
                {"robot_id": robot_id, "error_type": error_type, "reason": error.description},
            )
            raise

        emit_mission_lifecycle(self._events, command.mission_id, target, MissionTrigger.DISPATCH)
        await self._audit(
            "mission.dispatch",
            "mission",
            str(command.mission_id),
            {"robot_id": robot_id},
        )

        return (await self._repo.get(command.mission_id)) or mission

    async def cancel(self, command: CancelMissionCommand) -> Mission:
        return await self._apply_lifecycle_trigger(
            command.mission_id,
            MissionTrigger.CANCEL,
            dispatcher_action="cancel",
            audit_action="mission.cancel",
        )

    async def pause(self, command: PauseMissionCommand) -> Mission:
        return await self._apply_lifecycle_trigger(
            command.mission_id,
            MissionTrigger.PAUSE,
            dispatcher_action="pause",
            audit_action="mission.pause",
        )

    async def resume(self, command: ResumeMissionCommand) -> Mission:
        return await self._apply_lifecycle_trigger(
            command.mission_id,
            MissionTrigger.RESUME,
            dispatcher_action="resume",
            audit_action="mission.resume",
        )

    async def delete(self, command: DeleteMissionCommand) -> None:
        # Lock the row so the deletability check and the delete are atomic; otherwise a
        # concurrent dispatch could turn a DRAFT mission DISPATCHED between them and the
        # delete would wipe a mission the robot is now executing.
        record = await self._repo.get_record_for_update(command.mission_id)
        if record is None:
            raise MissionNotFoundError(command.mission_id)
        if record.status is not MissionStatus.DRAFT and not is_terminal(record.status):
            raise InvalidMissionTransition(record.status, "delete")
        await self._repo.delete(command.mission_id)
        await self._audit("mission.delete", "mission", str(command.mission_id), None)

    async def reset(self, command: ResetMissionCommand) -> Mission:
        mission = await self._repo.get(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)

        current = await self._repo.get_status(command.mission_id)
        next_state(
            current, MissionTrigger.RESET
        )  # raises InvalidMissionTransition if not resettable

        result = await self._repo.reset_to_draft(command.mission_id)
        if result is None:
            # The guarded reset CAS refused after the pre-checks passed: a concurrent reset or
            # transition already left the resettable state. get_status distinguishes a mission
            # that is now gone (404) from one that raced into a non-resettable state (409).
            current = await self._repo.get_status(command.mission_id)
            raise InvalidMissionTransition(current, "reset")

        # The prior run's resolved per-stage rows are dropped, so clear its latched state frame
        # too, or a re-dispatch would briefly replay the old run's stages to a WS subscriber.
        self._events.unlatch(event_topics.mission_topic(command.mission_id, "state"))
        emit_mission_lifecycle(
            self._events, command.mission_id, MissionStatus.DRAFT, MissionTrigger.RESET
        )
        await self._audit("mission.reset", "mission", str(command.mission_id), None)
        return (await self._repo.get(command.mission_id)) or result

    async def get(self, mission_id: UUID) -> Mission | None:
        return await self._repo.get(mission_id)

    async def list(self) -> list[Mission]:
        return await self._repo.list()

    async def _apply_lifecycle_trigger(
        self,
        mission_id: UUID,
        trigger: MissionTrigger,
        *,
        dispatcher_action: str,
        audit_action: str,
    ) -> Mission:
        record = await self._repo.get_record_for_update(mission_id)
        if record is None:
            raise MissionNotFoundError(mission_id)
        mission = record.mission
        current = record.status
        target = next_state(current, trigger)
        if await self._repo.update_status(mission_id, target) is None:
            # Defensive: the held row lock and next_state's terminal guard make this
            # unreachable; if reached, the mission is already settled, so change nothing more.
            return (await self._repo.get(mission_id)) or mission
        if is_terminal(target):
            # A cancel resolves the per-stage state from the rows reported so far (the active
            # stage becomes CANCELLED, later stages SKIPPED), before the robot is commanded.
            await self._finalize_stage_state(mission_id, mission, target)

        robot_id = await self._repo.get_assigned_robot(mission_id)
        if robot_id is not None and current not in _PRE_DISPATCH_STATES:
            await getattr(self._dispatcher, dispatcher_action)(mission_id, robot_id)

        emit_mission_lifecycle(self._events, mission_id, target, trigger)

        await self._audit(audit_action, "mission", str(mission_id), None)
        return (await self._repo.get(mission_id)) or mission

    async def _finalize_stage_state(
        self,
        mission_id: UUID,
        mission: Mission,
        mission_status: MissionStatus,
        failure_errors: list[MissionError] | None = None,
    ) -> None:
        """Resolve, persist, and publish the per-stage state at a terminal transition."""
        live_by_id = {rec.stage_id: rec for rec in await self._repo.get_stage_states(mission_id)}
        resolved = final_stage_statuses(
            mission.stages, live_by_id, mission_status, datetime.now(timezone.utc)
        )
        await self._repo.overwrite_stage_states(mission_id, resolved)
        view = build_mission_state_view(mission_id, resolved, failure_errors)
        self._events.publish(
            event_topics.mission_topic(mission_id, "state"),
            view.model_dump(mode="json"),
            latch=True,
        )

    async def _require_current_boundary(self, coverage: CoverageProvenance | None) -> None:
        """Reject a generated plan whose field no longer looks the way it was planned from.

        A hand-authored mission names no field and is not checked.
        """
        if coverage is None:
            return
        field = await self._fields.get(coverage.field_id)
        if field is None:
            raise StaleCoverageBoundary(coverage.field_id, "has been deleted")
        if boundary_digest(field.geometry) != coverage.boundary_digest:
            raise StaleCoverageBoundary(coverage.field_id, "has been edited")

    async def _validate_sites_exist(self, stages) -> None:
        """Reject stages referencing a site_id absent from the backend catalog.

        Complements the assign/dispatch-time factsheet check (UnknownSiteForRobot):
        this guards mission authoring against typo'd / deleted sites up front.
        """
        for site_id in referenced_site_ids(stages):
            if await self._sites.get(site_id) is None:
                raise UnknownSite(site_id)


_PRE_DISPATCH_STATES = frozenset({MissionStatus.DRAFT, MissionStatus.ASSIGNED})


def _with_ids(inputs: Sequence[StageInput]) -> list[Stage]:
    """Assign each requested stage its identity.

    Recurses into ``on_cancel`` because cleanup stages are stages: the robot reports their
    execution under the same ``stage_id`` join, so one without an id would be unattributable.
    """
    staged: list[Stage] = []
    for stage in inputs:
        on_cancel = _with_ids(stage.on_cancel) if stage.on_cancel else None
        if isinstance(stage, CoverageStageInput):
            staged.append(
                CoverageStage(
                    stage_id=uuid4(),
                    segments=[
                        Segment(kind=segment.kind, waypoints=segment.waypoints)
                        for segment in stage.segments
                    ],
                    on_cancel=on_cancel,
                )
            )
        else:
            staged.append(
                NavigationStage(
                    stage_id=uuid4(),
                    waypoints=stage.waypoints,
                    on_cancel=on_cancel,
                )
            )
    return staged


def _validate_homogeneity(stages) -> None:
    """All waypoints in a stage must share their ``kind`` discriminator."""
    for index, stage in enumerate(stages):
        kinds = {wp.kind for wp in stage_waypoints(stage)}
        if len(kinds) > 1:
            raise StageNotHomogeneous(index, kinds)


def _validate_against_factsheet(
    mission: Mission,
    robot_id: str,
    factsheet: RobotFactsheet,
) -> None:
    """Reject missions whose stage kinds or waypoint frames the robot has not declared.

    Site-instance gating (which specific sites the robot has a map for) is
    deferred with the site-local-navigation feature; today only the stage kind
    and the waypoint coordinate frame are gated.
    """
    for stage in mission.stages:
        try:
            kind = StageKind(stage.kind)
        except ValueError:
            raise UnsupportedStageKind(robot_id, stage.stage_id, stage.kind)

        # A robot declares each stage kind it can execute. Coverage is declared separately from
        # navigation because driving a swath as a line is a different claim from visiting points,
        # and a robot that has not declared it is refused the mission rather than sent it anyway.
        if kind is StageKind.NAVIGATION:
            capability = factsheet.navigation
        elif kind is StageKind.COVERAGE:
            capability = factsheet.coverage
        else:
            capability = None
        if capability is None:
            raise UnsupportedStageKind(robot_id, stage.stage_id, stage.kind)

        supported_frames = set(capability.supported_waypoint_kinds)
        for waypoint in stage_waypoints(stage):
            frame = WaypointKind(waypoint.kind)
            if frame not in supported_frames:
                raise UnsupportedWaypointFrame(robot_id, stage.stage_id, frame.value)


def _validate_plan_fits_robot(
    robot_id: str, coverage: CoverageProvenance | None, factsheet: RobotFactsheet
) -> None:
    """Reject a generated plan this machine cannot drive as it was laid out.

    A tighter-turning machine can follow wider turns, but the reverse cuts every corner silently.
    Width is checked against the implement rather than against the machine the plan was made for,
    because swath spacing follows the implement, and a machine wider than it runs its wheels over
    the strip just worked.
    """
    if coverage is None:
        return
    if factsheet.physical_parameters is None:
        raise RobotPhysicalParametersMissing(robot_id)
    robot_radius_m = factsheet.physical_parameters.min_turning_radius_m
    plan_radius_m = coverage.params.turning_radius_m
    if robot_radius_m > plan_radius_m:
        raise IncompatibleTurningRadius(robot_id, robot_radius_m, plan_radius_m)
    track_width_m = factsheet.physical_parameters.track_width_m
    operation_width_m = coverage.params.operation_width_m
    if track_width_m > operation_width_m:
        raise ImplementNarrowerThanRobot(robot_id, track_width_m, operation_width_m)
