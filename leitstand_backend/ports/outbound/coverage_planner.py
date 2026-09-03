"""Coverage planning port: turn a field boundary into a path that covers it."""

from __future__ import annotations

from abc import ABC, abstractmethod

from geojson_pydantic import Polygon

from leitstand_backend.domain.model.mission.coverage import CoverageParams, CoveragePlan


class CoveragePlanner(ABC):
    @abstractmethod
    async def plan(self, boundary: Polygon, params: CoverageParams) -> CoveragePlan:
        """Return a path covering ``boundary`` under ``params``.

        Raise ``CoveragePlanRejected`` when the boundary cannot be covered as asked, rather than
        returning an approximation: a plan an operator cannot tell apart from a correct one is
        worse than a refusal. Raise ``CoveragePlannerUnavailable`` when no planner answered, so
        the caller can distinguish an unusable request from an unusable service.
        """
