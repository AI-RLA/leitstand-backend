"""Bind the coverage stage planner function to in-memory fakes, as deps.py binds it to adapters."""

from __future__ import annotations

from uuid import UUID

from leitstand_backend.application.coverage_stage_planner import (
    CoverageInputs,
    CoverageStagePlanner,
    CoverageStageUnchanged,
    plan_coverage_stage,
    unchanged,
)
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoveragePlan,
    PlannedSegment,
)
from leitstand_backend.domain.model.mission.mission import CoverageStage
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.ports.outbound.field_repository import FieldRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from tests.fakes.fake_coverage_planner import FakeCoveragePlanner
from tests.fakes.planned_coverage import FIELD_POLYGON

TURN_SAMPLE_M = 0.25
LINEAR_CURV_CHANGE = 200.0

# One hectare at a 3 m working width: 2700 m of track sweeps 8100 m2, which passes validate_plan.
PLANNED_ANGLE_DEG = 42.5


def canned_plan() -> CoveragePlan:
    return CoveragePlan(
        segments=[
            PlannedSegment(
                kind="swath",
                waypoints=[WGS84Waypoint(lat=52.3, lon=8.05), WGS84Waypoint(lat=52.31, lon=8.05)],
            ),
            PlannedSegment(
                kind="turn",
                waypoints=[WGS84Waypoint(lat=52.31, lon=8.05), WGS84Waypoint(lat=52.31, lon=8.051)],
            ),
            PlannedSegment(
                kind="swath",
                waypoints=[WGS84Waypoint(lat=52.31, lon=8.051), WGS84Waypoint(lat=52.3, lon=8.051)],
            ),
        ],
        mainland_boundary=FIELD_POLYGON,
        swath_angle_deg=PLANNED_ANGLE_DEG,
        metrics=CoverageMetrics(
            swath_count=2, track_length_m=2700.0, covered_area_m2=8100.0, max_excursion_m=0.4
        ),
        planner_version="fields2cover 1.2.1",
    )


def bind_stage_planning(
    fields: FieldRepository,
    factsheets: RobotFactsheetView,
    planner: FakeCoveragePlanner | None = None,
) -> tuple[CoverageStagePlanner, CoverageStageUnchanged, FakeCoveragePlanner]:
    """Return the two callables a MissionManagementService takes, over ``planner``."""
    backend = planner or FakeCoveragePlanner(canned_plan())

    async def _plan(inputs: CoverageInputs, *, stage_id: UUID | None = None) -> CoverageStage:
        return await plan_coverage_stage(
            inputs,
            fields=fields,
            factsheets=factsheets,
            planner=backend,
            turn_sample_m=TURN_SAMPLE_M,
            linear_curv_change=LINEAR_CURV_CHANGE,
            stage_id=stage_id,
        )

    async def _unchanged(inputs: CoverageInputs, stored: CoverageStage) -> bool:
        return await unchanged(inputs, stored, fields=fields, factsheets=factsheets)

    return _plan, _unchanged, backend
