"""Fake CoveragePlanner (test-only)."""

from __future__ import annotations

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.mission.coverage import CoverageParams, CoveragePlan
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner


class FakeCoveragePlanner(CoveragePlanner):
    """Return a canned plan, or raise ``error`` when one is set.

    Records every call so a test can assert what reached the planner, which is the only place the
    field's own boundary can be observed leaving the backend.
    """

    def __init__(self, result: CoveragePlan) -> None:
        self.result = result
        self.error: Exception | None = None
        self.calls: list[tuple[Polygon, CoverageParams]] = []

    async def plan(self, boundary: Polygon, params: CoverageParams) -> CoveragePlan:
        self.calls.append((boundary, params))
        if self.error is not None:
            raise self.error
        return self.result
