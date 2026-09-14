"""The plausibility checks a returned coverage plan must pass."""

import pytest

from leitstand_backend.domain.errors import CoveragePlanRejected
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoverageParams,
    CoveragePlan,
    PlannedSegment,
    validate_plan,
)
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from tests.fakes.planned_coverage import FIELD_POLYGON

PARAMS = CoverageParams(operation_width_m=3.0, turning_radius_m=1.5, headland_width_m=1.5)


def _plan(
    swaths: int = 3, track_length_m: float = 100.0, covered_area_m2: float = 300.0
) -> CoveragePlan:
    segments = [
        PlannedSegment(
            kind="swath",
            waypoints=[WGS84Waypoint(lat=52.3, lon=8.05), WGS84Waypoint(lat=52.301, lon=8.05)],
        )
        for _ in range(swaths)
    ]
    return CoveragePlan(
        segments=segments,
        mainland_boundary=FIELD_POLYGON,
        metrics=CoverageMetrics(
            swath_count=3, track_length_m=track_length_m, covered_area_m2=covered_area_m2
        ),
        planner_version="fields2cover 1.2.1",
    )


def test_a_consistent_plan_passes() -> None:
    validate_plan(_plan(), field_area_m2=1000.0, params=PARAMS)


def test_a_swath_count_that_does_not_match_the_segments_is_rejected() -> None:
    with pytest.raises(CoveragePlanRejected, match="swaths"):
        validate_plan(_plan(swaths=2), field_area_m2=1000.0, params=PARAMS)


def test_too_little_covered_ground_is_rejected() -> None:
    with pytest.raises(CoveragePlanRejected, match="covers"):
        validate_plan(_plan(covered_area_m2=100.0), field_area_m2=1000.0, params=PARAMS)


def test_more_than_the_field_is_rejected() -> None:
    with pytest.raises(CoveragePlanRejected, match="covers"):
        validate_plan(
            _plan(covered_area_m2=1500.0, track_length_m=500.0), field_area_m2=1000.0, params=PARAMS
        )


def test_a_track_length_that_cannot_sweep_the_covered_area_is_rejected() -> None:
    with pytest.raises(CoveragePlanRejected, match="sweeps"):
        validate_plan(_plan(track_length_m=10.0), field_area_m2=1000.0, params=PARAMS)
