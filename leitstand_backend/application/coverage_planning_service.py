"""CoveragePlanningService - derive a draft mission from a field's own boundary."""

from __future__ import annotations

from uuid import UUID

from leitstand_backend.application.coverage_stage_planner import (
    CoverageInputs,
    plan_coverage_stage,
)
from leitstand_backend.domain.errors import CoveragePlanRejected, MissionArchived
from leitstand_backend.domain.model.mission.mission import Mission
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
        inputs = CoverageInputs(
            field_id=command.field_id,
            operation_width_m=command.operation_width_m,
            params_robot_id=command.robot_id,
            turning_radius_m=None,
            headland_width_m=command.headland_width_m,
            swath_angle_deg=command.swath_angle_deg,
            allow_overlap=command.allow_overlap,
        )
        stage = await plan_coverage_stage(
            inputs,
            fields=self._fields,
            factsheets=self._factsheets,
            planner=self._planner,
            turn_sample_m=self._turn_sample_m,
            linear_curv_change=self._linear_curv_change,
            stage_id=command.replan,
        )
        field_id = stage.provenance.field_id
        if command.replan is not None:
            # Overwriting the definition loses nothing: every run carries the stages it executed,
            # and the stage keeps its id so its runs stay linked to it.
            target = await self._stage_to_replan(command.replan, field_id)
            mission = await self._missions.replace_generated_stage(
                ReplaceGeneratedStageCommand(mission_id=target.mission_id, stage=stage)
            )
        else:
            mission = await self._missions.create_generated(
                CreateGeneratedMissionCommand(
                    name=command.name or await self._default_name(field_id),
                    description=command.description,
                    stages=[stage],
                )
            )
        return mission

    async def _default_name(self, field_id: UUID) -> str:
        """The field's name is the default; a mission name is only required to differ from it."""
        field = await self._fields.get(field_id)
        return f"Coverage of {field.name if field else field_id}"[:255]

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
