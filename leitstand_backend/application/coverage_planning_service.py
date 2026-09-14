"""CoveragePlanningService - derive a draft mission from a field's own boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.domain.errors import (
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    MissionArchived,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.mission.coverage import (
    CoverageParams,
    CoverageProvenance,
    boundary_digest,
    validate_plan,
)
from leitstand_backend.domain.model.mission.mission import (
    CoverageStage,
    Mission,
    Segment,
    StageKind,
)
from leitstand_backend.domain.model.mission.stage_rules import coverage_stages
from leitstand_backend.ports.inbound.coverage_planning import (
    CoveragePlanningUseCase,
    PlanCoverageCommand,
)
from leitstand_backend.ports.inbound.mission_management import (
    CreateGeneratedMissionCommand,
    MissionManagementUseCase,
    ReplaceGeneratedStageCommand,
)
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView

_SQUARE_METRES_PER_HECTARE = 10_000


class CoveragePlanningService(CoveragePlanningUseCase):
    def __init__(
        self,
        fields: FieldRepository,
        factsheets: RobotFactsheetView,
        planner: CoveragePlanner,
        missions: MissionManagementUseCase,
        repo: MissionRepository,
        turn_sample_m: float,
        linear_curv_change: float,
    ):
        self._fields = fields
        self._factsheets = factsheets
        self._planner = planner
        self._missions = missions
        self._repo = repo
        self._turn_sample_m = turn_sample_m
        self._linear_curv_change = linear_curv_change

    async def plan(self, command: PlanCoverageCommand) -> Mission:
        """Plan the field, then create the mission from the result.

        Nothing is written until a plan has been produced and checked, so a refused plan leaves
        no half-built mission for an operator to find and wonder about.
        """
        field = await self._fields.get(command.field_id)
        if field is None:
            raise FieldNotFoundError(command.field_id)
        if field.area_ha is None or field.area_ha <= 0:
            raise FieldNotPlannable(command.field_id)

        factsheet = self._factsheets.latest(command.robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(command.robot_id)
        if factsheet.physical_parameters is None:
            raise RobotPhysicalParametersMissing(command.robot_id)
        # Checked here rather than left to assign: a plan this robot cannot drive is worth
        # refusing before the operator reviews it, not after.
        if factsheet.coverage is None:
            raise UnsupportedStageKind(command.robot_id, uuid4(), StageKind.COVERAGE.value)

        turning_radius_m = factsheet.physical_parameters.min_turning_radius_m
        params = CoverageParams(
            operation_width_m=command.operation_width_m,
            # Taken from the machine's own declaration rather than the request, so a plan can
            # never be laid out for a turn the robot it names cannot make.
            turning_radius_m=turning_radius_m,
            # Fields2Cover constrains swaths to the field but never the turns joining them, so the
            # headland is the only thing keeping a turn inside the boundary and one turning radius
            # is what a turn needs, which an operator who knows the edge is driveable may reduce.
            headland_width_m=(
                turning_radius_m if command.headland_width_m is None else command.headland_width_m
            ),
            swath_angle_deg=command.swath_angle_deg,
            linear_curv_change=self._linear_curv_change,
            track_width_m=factsheet.physical_parameters.track_width_m,
            allow_overlap=command.allow_overlap,
            turn_sample_m=self._turn_sample_m,
        )

        field_area_m2 = field.area_ha * _SQUARE_METRES_PER_HECTARE
        plan = await self._planner.plan(field.geometry, params)
        # The planner picks a swath angle when the caller leaves it open, so the record of what
        # was planned takes it from the answer rather than from the question.
        params = params.model_copy(update={"swath_angle_deg": plan.swath_angle_deg})
        validate_plan(plan, field_area_m2, params)

        stage = CoverageStage(
            stage_id=uuid4(),
            segments=[Segment(kind=s.kind, waypoints=list(s.waypoints)) for s in plan.segments],
            provenance=CoverageProvenance(
                field_id=field.id,
                boundary_digest=boundary_digest(field.geometry),
                field_area_m2=field_area_m2,
                mainland_boundary=plan.mainland_boundary,
                params=params,
                metrics=plan.metrics,
                planner_version=plan.planner_version,
                planned_for_robot_id=command.robot_id,
                planned_at=datetime.now(timezone.utc),
            ),
        )
        if command.replan is not None:
            # Overwriting the definition loses nothing: every run carries the stages it executed,
            # and the stage keeps its id so its runs stay linked to it.
            target = await self._stage_to_replan(command.replan, field.id)
            mission = await self._missions.replace_generated_stage(
                ReplaceGeneratedStageCommand(
                    mission_id=target.mission_id,
                    stage=stage.model_copy(update={"stage_id": command.replan}),
                )
            )
        else:
            mission = await self._missions.create_generated(
                CreateGeneratedMissionCommand(
                    # The field's name is the default; a mission name is only required when the
                    # operator wants a different one.
                    name=command.name or f"Coverage of {field.name}"[:255],
                    description=command.description,
                    stages=[stage],
                )
            )
        return mission

    async def _stage_to_replan(self, stage_id: UUID, field_id: UUID) -> Mission:
        """Return the mission owning the coverage stage being re-planned, or refuse.

        Re-planning overwrites a path, so only this field's own generated stage may be re-planned;
        another field's plan or a hand-written stage is refused.
        """
        mission_id = await self._repo.mission_id_for_stage(stage_id)
        mission = await self._repo.get(mission_id) if mission_id else None
        stage = (
            next((s for s in coverage_stages(mission.stages) if s.stage_id == stage_id), None)
            if mission
            else None
        )
        if mission is None or stage is None:
            raise CoveragePlanRejected(f"no coverage stage {stage_id} exists to supersede")
        if mission.archived_at is not None:
            raise MissionArchived(mission.mission_id)
        if stage.provenance.field_id != field_id:
            raise CoveragePlanRejected(
                f"coverage stage {stage_id} covers a different field and cannot be superseded"
            )
        return mission
