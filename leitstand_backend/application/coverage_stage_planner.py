"""Plan one coverage stage from its inputs, or tell whether a stored plan still matches them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from leitstand_backend.domain.errors import (
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    TurningRadiusBelowRobot,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.field import Field
from leitstand_backend.domain.model.mission.coverage import (
    CoverageParams,
    CoverageProvenance,
    boundary_digest,
    validate_plan,
)
from leitstand_backend.domain.model.mission.mission import CoverageStage, Segment, StageKind
from leitstand_backend.ports.inbound.mission_management import CoveragePlanningFields
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView

_SQUARE_METRES_PER_HECTARE = 10_000

# What plan_coverage_stage raises for the inputs themselves; a missing field is separate because
# the routes answer it differently.
PLANNING_INPUT_ERRORS = (
    FieldNotPlannable,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    UnsupportedStageKind,
    TurningRadiusBelowRobot,
    CoveragePlanRejected,
)

# A radius the operator typed is compared with the robot's own after float round trips.
_RADIUS_TOLERANCE_M = 1e-9
_ANGLE_TOLERANCE_DEG = 1e-6


@dataclass(frozen=True)
class CoverageInputs:
    """What determines a coverage plan, as the operator or the assistant gave it."""

    field_id: UUID
    operation_width_m: float
    params_robot_id: str | None
    turning_radius_m: float | None
    headland_width_m: float | None
    swath_angle_deg: float | None
    allow_overlap: bool

    @classmethod
    def from_fields(cls, fields: CoveragePlanningFields) -> CoverageInputs:
        if fields.field_id is None or fields.operation_width_m is None:
            raise ValueError("a carried coverage stage has no planning inputs")
        return cls(
            field_id=fields.field_id,
            operation_width_m=fields.operation_width_m,
            params_robot_id=fields.params_robot_id,
            turning_radius_m=fields.turning_radius_m,
            headland_width_m=fields.headland_width_m,
            swath_angle_deg=fields.swath_angle_deg,
            allow_overlap=fields.allow_overlap,
        )


class CoverageStagePlanner(Protocol):
    async def __call__(
        self, inputs: CoverageInputs, *, stage_id: UUID | None = None
    ) -> CoverageStage: ...


class CoverageStageUnchanged(Protocol):
    async def __call__(self, inputs: CoverageInputs, stored: CoverageStage) -> bool: ...


@dataclass(frozen=True)
class _MachineValues:
    """The values the planner is given, with a record of where each came from."""

    turning_radius_m: float
    headland_width_m: float
    track_width_m: float | None
    sources: dict[str, str]


async def plan_coverage_stage(
    inputs: CoverageInputs,
    *,
    fields: FieldRepository,
    factsheets: RobotFactsheetView,
    planner: CoveragePlanner,
    turn_sample_m: float,
    linear_curv_change: float,
    stage_id: UUID | None = None,
) -> CoverageStage:
    """Plan one coverage stage from its inputs; never writes."""
    field = await _plannable_field(inputs.field_id, fields)
    machine = _machine_values(inputs, factsheets)
    params = CoverageParams(
        operation_width_m=inputs.operation_width_m,
        turning_radius_m=machine.turning_radius_m,
        headland_width_m=machine.headland_width_m,
        swath_angle_deg=inputs.swath_angle_deg,
        allow_overlap=inputs.allow_overlap,
        track_width_m=machine.track_width_m,
        linear_curv_change=linear_curv_change,
        turn_sample_m=turn_sample_m,
    )

    field_area_m2 = field.area_ha * _SQUARE_METRES_PER_HECTARE
    plan = await planner.plan(field.geometry, params)
    # The planner picks a swath angle when the caller leaves it open, so the record of what
    # was planned takes it from the answer rather than from the question.
    params = params.model_copy(update={"swath_angle_deg": plan.swath_angle_deg})
    validate_plan(plan, field_area_m2, params)
    sources = {
        **machine.sources,
        "swath_angle_deg": "manual" if inputs.swath_angle_deg is not None else "planner",
    }

    return CoverageStage(
        stage_id=stage_id or uuid4(),
        segments=[Segment(kind=s.kind, waypoints=list(s.waypoints)) for s in plan.segments],
        provenance=CoverageProvenance(
            field_id=field.id,
            boundary_digest=boundary_digest(field.geometry),
            field_area_m2=field_area_m2,
            mainland_boundary=plan.mainland_boundary,
            params=params,
            metrics=plan.metrics,
            planner_version=plan.planner_version,
            planned_for_robot_id=inputs.params_robot_id,
            param_sources=sources,
            planned_at=datetime.now(timezone.utc),
        ),
    )


async def unchanged(
    inputs: CoverageInputs,
    stored: CoverageStage,
    *,
    fields: FieldRepository,
    factsheets: RobotFactsheetView,
) -> bool:
    """True when planning ``inputs`` again would reproduce ``stored``.

    Deployment settings (turn sampling, curvature change) are not compared: they belong to the
    installation, not to the operator's inputs.
    """
    provenance = stored.provenance
    if provenance.param_sources is None:
        return False
    if inputs.field_id != provenance.field_id:
        return False
    field = await _plannable_field(inputs.field_id, fields)
    if boundary_digest(field.geometry) != provenance.boundary_digest:
        return False

    machine = _machine_values(inputs, factsheets)
    params = provenance.params
    if (
        inputs.operation_width_m != params.operation_width_m
        or machine.turning_radius_m != params.turning_radius_m
        or machine.headland_width_m != params.headland_width_m
        or machine.track_width_m != params.track_width_m
        or inputs.allow_overlap != bool(params.allow_overlap)
        or inputs.params_robot_id != provenance.planned_for_robot_id
    ):
        return False

    angle_source = provenance.param_sources.get("swath_angle_deg")
    if inputs.swath_angle_deg is None:
        return angle_source == "planner"
    # The stored angle is the planner's echo of the request, which may have passed through
    # radians on the way, so it is compared to the resolution a machine can steer, not bit for bit.
    return (
        angle_source == "manual"
        and params.swath_angle_deg is not None
        and abs(inputs.swath_angle_deg - params.swath_angle_deg) < _ANGLE_TOLERANCE_DEG
    )


async def _plannable_field(field_id: UUID, fields: FieldRepository) -> Field:
    field = await fields.get(field_id)
    if field is None:
        raise FieldNotFoundError(field_id)
    if field.area_ha is None or field.area_ha <= 0:
        raise FieldNotPlannable(field_id)
    return field


def _machine_values(inputs: CoverageInputs, factsheets: RobotFactsheetView) -> _MachineValues:
    """Resolve radius, headland and track width from the robot's factsheet or the inputs."""
    if inputs.params_robot_id is None:
        if inputs.turning_radius_m is None:
            raise ValueError("planning a coverage stage needs params_robot_id or turning_radius_m")
        radius = inputs.turning_radius_m
        track = None
        sources = {"turning_radius_m": "manual"}
    else:
        robot_id = inputs.params_robot_id
        factsheet = factsheets.latest(robot_id)
        if factsheet is None:
            raise RobotFactsheetMissing(robot_id)
        if factsheet.physical_parameters is None:
            raise RobotPhysicalParametersMissing(robot_id)
        # Checked here rather than left to assign: a plan this robot cannot drive is worth
        # refusing before the operator reviews it, not after.
        if factsheet.coverage is None:
            raise UnsupportedStageKind(robot_id, uuid4(), StageKind.COVERAGE.value)
        min_radius = factsheet.physical_parameters.min_turning_radius_m
        if inputs.turning_radius_m is None:
            radius = min_radius
            sources = {"turning_radius_m": f"factsheet:{robot_id}"}
        else:
            if inputs.turning_radius_m < min_radius - _RADIUS_TOLERANCE_M:
                raise TurningRadiusBelowRobot(robot_id, inputs.turning_radius_m, min_radius)
            radius = inputs.turning_radius_m
            sources = {"turning_radius_m": "manual"}
        track = factsheet.physical_parameters.track_width_m
        sources["track_width_m"] = f"factsheet:{robot_id}"

    # Fields2Cover constrains swaths to the field but never the turns joining them, so the
    # headland is the only thing keeping a turn inside the boundary and one turning radius is
    # what a turn needs, which an operator who knows the edge is driveable may reduce.
    if inputs.headland_width_m is None:
        headland = radius
        sources["headland_width_m"] = "turning_radius"
    else:
        headland = inputs.headland_width_m
        sources["headland_width_m"] = "manual"
    return _MachineValues(radius, headland, track, sources)
