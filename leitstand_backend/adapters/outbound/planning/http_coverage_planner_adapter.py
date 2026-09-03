"""CoveragePlanner backed by the coverage planner service over HTTP."""

from __future__ import annotations

import httpx
from geojson_pydantic import Polygon

from leitstand_backend.domain.errors import CoveragePlannerUnavailable, CoveragePlanRejected
from leitstand_backend.domain.model.mission.coverage import (
    CoverageMetrics,
    CoverageParams,
    CoveragePlan,
    PlannedSegment,
)
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.ports.outbound.coverage_planner import CoveragePlanner


class HttpCoveragePlannerAdapter(CoveragePlanner):
    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client

    async def plan(self, boundary: Polygon, params: CoverageParams) -> CoveragePlan:
        payload = {
            "boundary": boundary.model_dump(mode="json"),
            "operation_width_m": params.operation_width_m,
            "turning_radius_m": params.turning_radius_m,
            "headland_width_m": params.headland_width_m,
            "swath_angle_deg": params.swath_angle_deg,
            "linear_curv_change": params.linear_curv_change,
            "track_width_m": params.track_width_m,
            "allow_overlap": params.allow_overlap,
            "turn_sample_m": params.turn_sample_m,
        }
        try:
            response = await self._client.post(f"{self._base_url}/plan", json=payload)
        except httpx.HTTPError as exc:
            raise CoveragePlannerUnavailable(f"coverage planner unreachable: {exc}") from exc

        if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            detail = _detail(response)
            # The planner refuses a field with a sentence and rejects a request it cannot parse
            # with a list, so a list means the two services disagree about the contract and the
            # operator must not be told to correct numbers that were never the problem.
            if not isinstance(detail, str):
                raise CoveragePlannerUnavailable(
                    f"coverage planner rejected the request itself: {detail}"
                )
            raise CoveragePlanRejected(detail)
        if response.status_code != httpx.codes.OK:
            raise CoveragePlannerUnavailable(
                f"coverage planner returned {response.status_code}: {_detail(response)}"
            )

        try:
            body = response.json()
            metrics = body["metrics"]
            # Mapped field by field rather than splatted, so a renamed or dropped field on the
            # planner fails here instead of silently becoming a default.
            return CoveragePlan(
                segments=[
                    PlannedSegment(
                        kind=segment["kind"],
                        waypoints=[
                            WGS84Waypoint(
                                lat=w["lat"], lon=w["lon"], heading_deg=w.get("heading_deg")
                            )
                            for w in segment["waypoints"]
                        ],
                    )
                    for segment in body["segments"]
                ],
                mainland_boundary=Polygon.model_validate(body["mainland_boundary"]),
                swath_angle_deg=body["swath_angle_deg"],
                metrics=CoverageMetrics(
                    swath_count=metrics["swath_count"],
                    track_length_m=metrics["track_length_m"],
                    path_length_m=metrics["path_length_m"],
                    covered_area_m2=metrics["covered_area_m2"],
                    # Read leniently so a planner that predates the field degrades to the older
                    # whole-field check rather than failing every plan.
                    mainland_area_m2=metrics.get("mainland_area_m2"),
                    max_excursion_m=metrics["max_excursion_m"],
                ),
                planner_version=body["planner_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CoveragePlannerUnavailable(f"coverage planner replied unusably: {exc}") from exc


def _detail(response: httpx.Response) -> object:
    """Return the response's ``detail`` as it arrived, so its shape stays readable to the caller."""
    try:
        return response.json().get("detail", response.text)
    except ValueError:
        return response.text
