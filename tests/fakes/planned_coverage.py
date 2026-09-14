"""Builders for coverage stages as a planner would have produced them.

A coverage stage cannot be authored, so a test that needs one needs its provenance too. The
defaults describe a machine that turns on the spot working a 3 m implement, which passes the
kinematic guard; a test that wants the guard to fail overrides the parameters it checks.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.field import Field
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoverageParams,
    CoverageProvenance,
    boundary_digest,
)
from leitstand_backend.domain.model.mission.mission import CoverageStage, Segment, Stage
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint

PLANNED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def coverage_provenance(
    *,
    field_id: UUID | None = None,
    boundary_digest: str = "digest",
    operation_width_m: float = 3.0,
    turning_radius_m: float = 1.5,
    planned_for_robot_id: str = "planner-robot",
) -> CoverageProvenance:
    return CoverageProvenance(
        field_id=field_id or uuid4(),
        boundary_digest=boundary_digest,
        field_area_m2=1000.0,
        params=CoverageParams(
            operation_width_m=operation_width_m,
            turning_radius_m=turning_radius_m,
            headland_width_m=turning_radius_m,
        ),
        metrics=CoverageMetrics(swath_count=3, track_length_m=100.0, covered_area_m2=900.0),
        planner_version="fields2cover v1.2.1",
        planned_for_robot_id=planned_for_robot_id,
        planned_at=PLANNED_AT,
    )


def coverage_stage(
    *,
    stage_id: UUID | None = None,
    lat: float = 52.3,
    provenance: CoverageProvenance | None = None,
    on_cancel: list[Stage] | None = None,
    **provenance_kwargs: object,
) -> CoverageStage:
    return CoverageStage(
        stage_id=stage_id or uuid4(),
        segments=[
            Segment(
                kind="swath",
                waypoints=[
                    WGS84Waypoint(lat=lat, lon=8.05),
                    WGS84Waypoint(lat=lat + 0.001, lon=8.05),
                ],
            )
        ],
        provenance=provenance or coverage_provenance(**provenance_kwargs),  # type: ignore[arg-type]
        on_cancel=on_cancel,
    )


FIELD_POLYGON = Polygon(
    type="Polygon",
    coordinates=[[(8.05, 52.3), (8.06, 52.3), (8.06, 52.31), (8.05, 52.31), (8.05, 52.3)]],
)


def seeded_field(fields) -> Field:
    """Put a field in the catalog so a planned stage's boundary check has something to match."""
    field = Field(
        id=uuid4(),
        name="north",
        geometry=FIELD_POLYGON,
        area_ha=1.0,
        notes=None,
        created_at=PLANNED_AT,
        updated_at=PLANNED_AT,
    )
    fields.seed(field)
    return field


def coverage_stage_for(field: Field, **kwargs: object) -> CoverageStage:
    """A coverage stage whose provenance names this field as it currently stands."""
    return coverage_stage(
        field_id=field.id,
        boundary_digest=boundary_digest(field.geometry),
        **kwargs,  # type: ignore[arg-type]
    )
