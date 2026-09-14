"""Coverage planning: the parameters, the resulting path, and how a mission derived from one."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from geojson_pydantic import Polygon
from pydantic import BaseModel, ConfigDict, Field

from leitstand_backend.domain.errors import CoveragePlanRejected
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint


class CoverageParams(BaseModel):
    """The inputs that determine a coverage path, as the planner was actually given them.

    The turning radius reaches here from the robot's own declaration rather than from the caller,
    so the two cannot disagree. The working width does not: an implement is mounted per job, so
    only the operator knows what is on the machine today.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    operation_width_m: float = Field(gt=0, description="Working width of the implement, in metres.")
    turning_radius_m: float = Field(
        ge=0,
        description="Minimum turning radius in metres; 0 for a robot that turns on the spot.",
    )
    headland_width_m: float = Field(
        ge=0,
        description=(
            "Turning space kept inside the boundary, in metres. Records what the plan was built "
            "with, which is the robot's own turning radius unless the operator chose otherwise."
        ),
    )
    swath_angle_deg: float | None = Field(
        default=None,
        ge=0,
        lt=180,
        description="Bearing of the swath lines; the planner chooses one when omitted.",
    )
    allow_overlap: bool | None = Field(
        default=None,
        description=(
            "Whether the last pass may overlap the one before it to cover the remainder. "
            "Absent on a plan made before it was recorded."
        ),
    )
    track_width_m: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Distance between the machine's wheels in metres, which the implement must at least "
            "span. Absent on a plan made before it was recorded."
        ),
    )
    linear_curv_change: float | None = Field(
        default=None,
        gt=0,
        description=(
            "How fast curvature may change along a turn, in 1/m2. Belongs to the machine as the "
            "turning radius does. Absent on a plan made before it was recorded."
        ),
    )
    turn_sample_m: float | None = Field(
        default=None,
        gt=0,
        description=(
            "How closely a turn is sampled, in metres. A turn curves, so its shape survives only "
            "in the points taken along it. Absent on a plan made before it was recorded."
        ),
    )


class CoverageMetrics(BaseModel):
    """What an operator needs in order to judge a plan before dispatching it."""

    model_config = ConfigDict(allow_inf_nan=False)

    swath_count: int = Field(ge=1)
    track_length_m: float = Field(
        gt=0,
        description=(
            "Total swath length, the ground actually worked. Excludes the turns, so it is what "
            "the covered area is checked against rather than how far the machine drives."
        ),
    )
    path_length_m: float | None = Field(
        default=None, ge=0, description="How far the machine drives in total, turns included."
    )
    covered_area_m2: float = Field(gt=0)
    mainland_area_m2: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Ground the swaths were allowed to work, which is the field less its headland. "
            "Absent on a plan plotted before it was reported."
        ),
    )
    max_excursion_m: float | None = Field(
        default=None,
        ge=0,
        description=(
            "How far outside the boundary the driven path reaches, in metres. A turn needs a "
            "headland at least as deep as the turning radius to be contained, and fields are "
            "rarely laid out that generously, so a correct plan routinely leaves the polygon. "
            "Whether that is a manoeuvre or a collision depends on what the boundary is made of, "
            "which only the operator knows."
        ),
    )


class PlannedSegment(BaseModel):
    """One swath across the field, or the turn joining two of them, as the planner produced it."""

    kind: Literal["swath", "turn"]
    waypoints: list[WGS84Waypoint] = Field(min_length=2)


class CoveragePlan(BaseModel):
    """A path covering one field boundary, with the metrics that describe it."""

    segments: list[PlannedSegment] = Field(
        min_length=1,
        description=(
            "The whole plan as one ordered route. Concatenating the segments gives the drivable "
            "line, turns included; the kinds say which of it is a worked swath. Consecutive "
            "segments share an endpoint, which a consumer joining them skips."
        ),
    )
    mainland_boundary: Polygon = Field(
        description="The ground the swaths were allowed to work, as the planner cut it."
    )
    swath_angle_deg: float | None = Field(
        default=None,
        ge=0,
        lt=180,
        description="Bearing the swaths were laid out on, as the planner resolved it.",
    )
    metrics: CoverageMetrics
    planner_version: str = Field(
        min_length=1, description="Planner name and version, e.g. 'fields2cover 1.2.1'."
    )


class CoverageProvenance(BaseModel):
    """How a coverage mission came to exist.

    Held with the mission rather than derived on demand: the field may be edited or deleted
    afterwards, and a generated path can only be judged, reproduced, or refused for the wrong
    robot against the inputs it was actually made from.
    """

    field_id: UUID
    boundary_digest: str = Field(min_length=1)
    field_area_m2: float = Field(gt=0)
    mainland_boundary: Polygon | None = Field(
        default=None,
        description=(
            "The ground the swaths were allowed to work: the field less its headland. Held here "
            "with the rest of the geometry this plan was made against, since the field itself may "
            "be edited or deleted afterwards. Absent on a plan made before it was recorded."
        ),
    )
    params: CoverageParams
    metrics: CoverageMetrics
    planner_version: str = Field(min_length=1)
    planned_for_robot_id: str = Field(min_length=1)
    planned_at: datetime


def boundary_digest(boundary: Polygon) -> str:
    """Return a stable digest of a field boundary, so an edit after planning is detectable."""
    return hashlib.sha256(boundary.model_dump_json().encode()).hexdigest()


# The floor excludes the headland (withheld, not missed); the ceiling includes it (swaths overhang).
_MIN_COVERED_FRACTION = 0.25
_MAX_COVERED_FRACTION = 1.25

# Track length times working width re-measures the covered area; a twofold gap means one is wrong.
_MAX_SWEPT_AREA_RATIO = 2.0


def validate_plan(plan: CoveragePlan, field_area_m2: float, params: CoverageParams) -> None:
    """Reject a plan whose numbers do not describe the field it claims to cover."""
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
