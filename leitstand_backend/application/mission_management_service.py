"""MissionManagementService: operator CRUD on mission definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.application.mission_validation import (
    require_current_boundary,
    validate_against_factsheet,
    validate_plan_fits_robot,
)
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.errors import (
    DuplicateStageId,
    MissionArchived,
    MissionNotArchived,
    MissionNotFoundError,
    MissionRunInProgress,
    RobotBusy,
    RobotFactsheetMissing,
    StageNotHomogeneous,
    StageNotInMission,
    StageSpansSites,
    UnknownSite,
)
from leitstand_backend.domain.model.mission.mission import (
    CoverageStage,
    Mission,
    NavigationStage,
    Stage,
    referenced_site_ids,
    replace_stage,
    stage_waypoints,
)
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint
from leitstand_backend.ports.inbound.mission_management import (
    AssignMissionCommand,
    CoverageStageRef,
    CreateGeneratedMissionCommand,
    CreateMissionCommand,
    DeleteMissionCommand,
    MissionManagementUseCase,
    ReplaceGeneratedStageCommand,
    RestoreMissionCommand,
    StageInput,
    UnassignMissionCommand,
    UpdateMissionCommand,
)
from leitstand_backend.ports.outbound.audit_log import AuditWriter
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from leitstand_backend.ports.outbound.site_repository import SiteRepository


class MissionManagementService(MissionManagementUseCase):
    def __init__(
        self,
        repo: MissionRepository,
        runs: MissionRunRepository,
        factsheets: RobotFactsheetView,
        events: EventPublisher,
        audit: AuditWriter,
        sites: SiteRepository,
        fields: FieldRepository,
    ):
        self._repo = repo
        self._runs = runs
        self._factsheets = factsheets
        self._events = events
        self._audit = audit
        self._sites = sites
        self._fields = fields

    async def create(self, command: CreateMissionCommand) -> Mission:
        stages = _with_ids(command.stages, existing=frozenset(), mission_id=None)
        await self._validate_shape(stages)

        now = datetime.now(timezone.utc)
        mission = Mission(
            mission_id=uuid4(),
            name=command.name,
            description=command.description,
            stages=stages,
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
        # Under the row lock so a concurrent dispatch snapshots either the old stages or the new,
        # never a mix. Editing while a run is active is fine: the run carries its own copy.
        before = await self._repo.get_for_update(command.mission_id)
        if before is None:
            raise MissionNotFoundError(command.mission_id)
        if before.archived_at is not None:
            raise MissionArchived(command.mission_id)

        stages = before.stages
        if command.stages is not None:
            stored = _by_stage_id(before.stages)
            stages = _with_ids(
                command.stages,
                existing=frozenset(stored),
                mission_id=before.mission_id,
                stored=stored,
            )
            await self._validate_shape(stages)

        updated = before.model_copy(
            update={
                "name": command.name if command.name is not None else before.name,
                "description": (
                    command.description if command.description is not None else before.description
                ),
                "stages": stages,
                "updated_at": datetime.now(timezone.utc),
            }
        )
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

    async def create_generated(self, command: CreateGeneratedMissionCommand) -> Mission:
        """Create a mission from stages a planner produced, provenance already attached."""
        await self._validate_shape(command.stages)
        now = datetime.now(timezone.utc)
        mission = Mission(
            mission_id=uuid4(),
            name=command.name,
            description=command.description,
            stages=command.stages,
            created_at=now,
            updated_at=now,
        )
        saved = await self._repo.save(mission)
        await self._audit(
            "mission.create",
            "mission",
            str(saved.mission_id),
            {"name": saved.name, "stage_count": len(saved.stages), "generated": True},
        )
        return saved

    async def replace_generated_stage(self, command: ReplaceGeneratedStageCommand) -> Mission:
        """Overwrite one generated stage in place, leaving the mission's other stages alone."""
        before = await self._repo.get_for_update(command.mission_id)
        if before is None:
            raise MissionNotFoundError(command.mission_id)
        if before.archived_at is not None:
            raise MissionArchived(command.mission_id)
        replaced, found = replace_stage(before.stages, command.stage)
        if not found:
            raise StageNotInMission(command.mission_id, command.stage.stage_id)
        await self._validate_shape(replaced)
        saved = await self._repo.save(
            before.model_copy(update={"stages": replaced, "updated_at": datetime.now(timezone.utc)})
        )
        await self._audit(
            "mission.replan",
            "mission",
            str(saved.mission_id),
            {"stage_id": str(command.stage.stage_id)},
        )
        return saved

    async def assign(self, command: AssignMissionCommand) -> Mission:
        """Set the default robot, running every check a dispatch would, as an early warning."""
        mission = await self._repo.get_for_update(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)
        if mission.archived_at is not None:
            raise MissionArchived(command.mission_id)

        holders = await self._runs.list_active_by_robot(command.robot_id)
        for other in holders:
            if other.mission_id != command.mission_id:
                raise RobotBusy(command.robot_id, other.mission_id)

        factsheet = self._factsheets.latest(command.robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(command.robot_id)
        validate_against_factsheet(mission, command.robot_id, factsheet)
        validate_plan_fits_robot(command.robot_id, mission.stages, factsheet)
        await require_current_boundary(self._fields, mission.stages)

        await self._repo.set_assigned_robot(command.mission_id, command.robot_id)
        await self._audit(
            "mission.assign",
            "mission",
            str(command.mission_id),
            {"robot_id": command.robot_id},
        )
        return (await self._repo.get(command.mission_id)) or mission

    async def unassign(self, command: UnassignMissionCommand) -> Mission:
        """Clear the default robot. A run already under way keeps the robot it was given."""
        mission = await self._repo.get_for_update(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)
        if mission.archived_at is not None:
            raise MissionArchived(command.mission_id)
        await self._repo.set_assigned_robot(command.mission_id, None)
        await self._audit("mission.unassign", "mission", str(command.mission_id), {})
        return (await self._repo.get(command.mission_id)) or mission

    async def delete(self, command: DeleteMissionCommand) -> None:
        """Delete a mission that never ran; archive one that did; refuse one with an active run.

        Deleting a mission with runs would take its run history with it, so it is archived
        instead and stays readable. An active run means a robot may be driving it.
        """
        mission = await self._repo.get_for_update(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)
        active = await self._runs.list_active_by_mission(command.mission_id)
        if active:
            raise MissionRunInProgress(command.mission_id, [run.run_id for run in active])
        if await self._runs.count_by_mission(command.mission_id):
            await self._repo.archive(command.mission_id)
            await self._audit("mission.archive", "mission", str(command.mission_id), {})
            return
        await self._repo.delete(command.mission_id)
        await self._audit("mission.delete", "mission", str(command.mission_id), {})

    async def restore(self, command: RestoreMissionCommand) -> Mission:
        """Bring an archived mission back. One that is not archived has nothing to restore."""
        mission = await self._repo.get_for_update(command.mission_id)
        if mission is None:
            raise MissionNotFoundError(command.mission_id)
        if mission.archived_at is None:
            raise MissionNotArchived(command.mission_id)
        restored = await self._repo.restore(command.mission_id)
        if restored is None:
            raise MissionNotFoundError(command.mission_id)
        await self._audit("mission.restore", "mission", str(command.mission_id), {})
        return restored

    async def get(self, mission_id: UUID) -> Mission | None:
        return await self._repo.get(mission_id)

    async def list(self, *, include_archived: bool = False) -> list[Mission]:
        return await self._repo.list(include_archived=include_archived)

    async def _validate_shape(self, stages: list[Stage]) -> None:
        """Every stage is driven in one frame, at sites that exist, and at one site at most.

        In that order, so a mixed-frame stage or a mistyped site gets its own error instead of
        the generic one.
        """
        _validate_homogeneity(stages)
        await self._validate_sites_exist(stages)
        _validate_single_site(stages)

    async def _validate_sites_exist(self, stages: list[Stage]) -> None:
        """Reject stages referencing a site_id absent from the backend catalog.

        Complements the assign/dispatch-time factsheet check (UnknownSiteForRobot): this guards
        mission authoring against mistyped or deleted sites up front.
        """
        for site_id in referenced_site_ids(stages):
            if await self._sites.get(site_id) is None:
                raise UnknownSite(site_id)


def _by_stage_id(stages: Sequence[Stage]) -> dict[UUID, Stage]:
    """Every stage of a mission by id, cleanup stages included."""
    found: dict[UUID, Stage] = {}
    for stage in stages:
        found[stage.stage_id] = stage
        if stage.on_cancel:
            found.update(_by_stage_id(stage.on_cancel))
    return found


def _with_ids(
    inputs: Sequence[StageInput],
    *,
    existing: frozenset[UUID],
    mission_id: UUID | None,
    stored: Mapping[UUID, Stage] | None = None,
) -> list[Stage]:
    """Give each requested stage its identity: kept when the caller names one, assigned otherwise.

    Recurses into ``on_cancel`` because the robot reports cleanup stages under the same
    ``stage_id`` join. A supplied id must be one of this mission's and appear once; a coverage
    stage is resolved from ``stored``, because only the planner writes its path.
    """
    seen: set[UUID] = set()
    by_id = stored or {}

    def build(stages: Sequence[StageInput]) -> list[Stage]:
        staged: list[Stage] = []
        for stage in stages:
            if isinstance(stage, CoverageStageRef):
                if stage.stage_id in seen:
                    raise DuplicateStageId(stage.stage_id)
                kept = by_id.get(stage.stage_id)
                if not isinstance(kept, CoverageStage):
                    raise StageNotInMission(mission_id, stage.stage_id)
                seen.add(stage.stage_id)
                staged.append(kept)
                continue
            stage_id = stage.stage_id
            if stage_id is not None:
                if stage_id in seen:
                    raise DuplicateStageId(stage_id)
                if stage_id not in existing:
                    raise StageNotInMission(mission_id, stage_id)
                seen.add(stage_id)
            else:
                stage_id = uuid4()
            on_cancel = build(stage.on_cancel) if stage.on_cancel else None
            staged.append(
                NavigationStage(
                    stage_id=stage_id,
                    waypoints=stage.waypoints,
                    on_cancel=on_cancel,
                )
            )
        return staged

    return build(inputs)


def _validate_homogeneity(stages: list[Stage]) -> None:
    """All waypoints in a stage must share their ``kind`` discriminator."""
    for index, stage in enumerate(stages):
        kinds = {wp.kind for wp in stage_waypoints(stage)}
        if len(kinds) > 1:
            raise StageNotHomogeneous(index, kinds)
        if stage.on_cancel:
            _validate_homogeneity(stage.on_cancel)


def _validate_single_site(stages: list[Stage]) -> None:
    """A site-local stage is driven in one site's frame; a WGS84 stage names none."""
    for index, stage in enumerate(stages):
        sites = {wp.site_id for wp in stage_waypoints(stage) if isinstance(wp, SiteLocalWaypoint)}
        if len(sites) > 1:
            raise StageSpansSites(index, sites)
        if stage.on_cancel:
            _validate_single_site(stage.on_cancel)
