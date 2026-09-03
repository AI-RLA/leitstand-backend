"""CoveragePlanningService - derive a draft mission from a field's own boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.domain.errors import (
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    MissionNotFoundError,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.mission.coverage import (
    CoverageParams,
    CoveragePlan,
    CoverageProvenance,
    boundary_digest,
)
from leitstand_backend.domain.model.mission.mission import Mission, StageKind
from leitstand_backend.ports.inbound.coverage_planning import (
    CoveragePlanningUseCase,
    PlanCoverageCommand,
)
from leitstand_backend.ports.inbound.mission_management import (
    CoverageStageInput,
    CreateMissionCommand,
    DeleteMissionCommand,
    MissionManagementUseCase,
    SegmentInput,
)
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView

_SQUARE_METRES_PER_HECTARE = 10_000

# The floor excludes the headland (withheld, not missed); the ceiling includes it (swaths overhang).
_MIN_COVERED_FRACTION = 0.25
_MAX_COVERED_FRACTION = 1.25

# Track length times working width re-measures the covered area; a twofold gap means one is wrong.
_MAX_SWEPT_AREA_RATIO = 2.0


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

        if command.replaces is not None:
            await self._check_replaceable(command.replaces, field.id)

        field_area_m2 = field.area_ha * _SQUARE_METRES_PER_HECTARE
        plan = await self._planner.plan(field.geometry, params)
        # The planner picks a swath angle when the caller leaves it open, so the record of what
        # was planned takes it from the answer rather than from the question.
        params = params.model_copy(update={"swath_angle_deg": plan.swath_angle_deg})
        _validate(plan, field_area_m2, params)

        mission = await self._missions.create(
            CreateMissionCommand(
                # An operator who asked for a field to be covered has already said what this is,
                # so a name is only worth demanding when they want a different one.
                name=command.name or f"Coverage of {field.name}"[:255],
                description=command.description,
                stages=[
                    CoverageStageInput(
                        segments=[
                            SegmentInput(kind=s.kind, waypoints=list(s.waypoints))
                            for s in plan.segments
                        ],
                    )
                ],
            )
        )
        await self._repo.save_coverage_provenance(
            mission.mission_id,
            CoverageProvenance(
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
        if command.replaces is not None:
            # Through the use case rather than the repository, so the superseded mission is locked,
            # refused while a robot is running it, and audited, and ordered after the new plan
            # exists because on the same transaction a refusal here takes both.
            await self._missions.delete(DeleteMissionCommand(mission_id=command.replaces))
        return mission

    async def _check_replaceable(self, mission_id: UUID, field_id: UUID) -> None:
        """Reject superseding anything but this field's own generated plan.

        A hand-authored mission is not a plan of this field that was re-derived, and neither is a
        plan of somewhere else, so replacing either would delete work nobody asked to lose.
        """
        record = await self._repo.get_record(mission_id)
        if record is None:
            raise MissionNotFoundError(mission_id)
        if record.coverage is None:
            raise CoveragePlanRejected(
                f"mission {mission_id} was not planned from a field and cannot be superseded"
            )
        if record.coverage.field_id != field_id:
            raise CoveragePlanRejected(
                f"mission {mission_id} covers a different field and cannot be superseded"
            )


def _validate(plan: CoveragePlan, field_area_m2: float, params: CoverageParams) -> None:
    """Reject a plan that does not describe the field it claims to cover."""
    # Cheapest disagreement to catch: metrics that do not describe the geometry they arrived with.
    swaths = sum(1 for seg in plan.segments if seg.kind == "swath")
    if plan.metrics.swath_count != swaths:
        raise CoveragePlanRejected(
            f"the plan reports {plan.metrics.swath_count} swaths but carries {swaths}"
        )

    covered = plan.metrics.covered_area_m2
    mainland = plan.metrics.mainland_area_m2 or field_area_m2
    if covered / mainland < _MIN_COVERED_FRACTION:
        raise CoveragePlanRejected(
            f"the plan covers {covered:.0f} m2 of the {mainland:.0f} m2 that a "
            f"{params.headland_width_m} m headland leaves as mainland in a "
            f"{field_area_m2:.0f} m2 field"
        )
    if covered / field_area_m2 > _MAX_COVERED_FRACTION:
        raise CoveragePlanRejected(
            f"the plan covers {covered:.0f} m2 of a {field_area_m2:.0f} m2 field"
        )

    swept = plan.metrics.track_length_m * params.operation_width_m
    if not covered / _MAX_SWEPT_AREA_RATIO <= swept <= covered * _MAX_SWEPT_AREA_RATIO:
        raise CoveragePlanRejected(
            f"{plan.metrics.track_length_m:.0f} m of track at {params.operation_width_m} m wide "
            f"sweeps {swept:.0f} m2, not the {covered:.0f} m2 reported"
        )
