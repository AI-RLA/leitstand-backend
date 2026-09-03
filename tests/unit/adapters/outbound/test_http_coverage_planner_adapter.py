"""HttpCoveragePlannerAdapter: a refusal and an outage must not look alike.

The service tells the two apart to decide whether the operator should fix the request or wait,
and the route maps them to 422 and 503. Getting the mapping wrong tells an operator to change
numbers that were never the problem.
"""

from __future__ import annotations

import httpx
import pytest
from geojson_pydantic import Polygon

from leitstand_backend.adapters.outbound.planning.http_coverage_planner_adapter import (
    HttpCoveragePlannerAdapter,
)
from leitstand_backend.domain.errors import CoveragePlannerUnavailable, CoveragePlanRejected
from leitstand_backend.domain.model.mission.coverage import CoverageParams

BASE_URL = "http://coverage-planner:8090"

_PLAN_BODY = {
    "segments": [
        {
            "kind": "swath",
            "waypoints": [
                {"lat": 52.0, "lon": 8.0, "heading_deg": 0.0},
                {"lat": 52.001, "lon": 8.0, "heading_deg": 0.0},
            ],
        },
        {
            "kind": "turn",
            "waypoints": [
                {"lat": 52.001, "lon": 8.0, "heading_deg": 0.0},
                {"lat": 52.001, "lon": 8.00004, "heading_deg": 90.0},
            ],
        },
    ],
    "mainland_boundary": {
        "type": "Polygon",
        "coordinates": [
            [
                [8.0001, 52.0001],
                [8.0009, 52.0001],
                [8.0009, 52.0009],
                [8.0001, 52.0009],
                [8.0001, 52.0001],
            ]
        ],
    },
    "swath_angle_deg": 42.5,
    "metrics": {
        "swath_count": 4,
        "path_length_m": 61.4,
        "track_length_m": 52.9,
        "covered_area_m2": 158.7,
        "max_excursion_m": 2.48,
    },
    "planner_version": "fields2cover v1.2.1",
}


def _boundary() -> Polygon:
    return Polygon(
        type="Polygon",
        coordinates=[[[8.0, 52.0], [8.001, 52.0], [8.001, 52.001], [8.0, 52.001], [8.0, 52.0]]],
    )


def _params() -> CoverageParams:
    return CoverageParams(operation_width_m=3.0, turning_radius_m=1.5, headland_width_m=0.5)


def _adapter(handler) -> HttpCoveragePlannerAdapter:
    """The adapter over a client that answers with ``handler`` instead of dialling anything."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpCoveragePlannerAdapter(BASE_URL, client)


@pytest.mark.asyncio
async def test_a_plan_is_mapped_into_the_domain():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json=_PLAN_BODY)

    plan = await _adapter(handler).plan(_boundary(), _params())

    assert captured["url"] == f"{BASE_URL}/plan"
    assert "operation_width_m" in captured["json"]
    assert len(plan.segments) == 2
    assert plan.segments[0].kind == "swath"
    assert plan.segments[0].waypoints[0].lat == 52.0
    assert plan.segments[1].kind == "turn"
    assert tuple(plan.mainland_boundary.coordinates[0][0]) == (8.0001, 52.0001)
    assert plan.swath_angle_deg == 42.5
    assert plan.metrics.swath_count == 4
    assert plan.metrics.path_length_m == 61.4
    assert plan.metrics.covered_area_m2 == 158.7
    assert plan.planner_version == "fields2cover v1.2.1"
    # Losing the path in the mapping would leave a plan that validates, dispatches, and covers
    # the field by a route nobody chose. Concatenated with the shared junction dropped.
    route = plan.segments[0].waypoints + plan.segments[1].waypoints[1:]
    assert [w.heading_deg for w in route] == [0.0, 0.0, 90.0]
    assert plan.metrics.max_excursion_m == 2.48


@pytest.mark.asyncio
async def test_a_refused_field_is_not_reported_as_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "boundary encloses no area"})

    with pytest.raises(CoveragePlanRejected) as exc:
        await _adapter(handler).plan(_boundary(), _params())

    assert "boundary encloses no area" in str(exc.value)


@pytest.mark.asyncio
async def test_an_unreachable_planner_is_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(CoveragePlannerUnavailable):
        await _adapter(handler).plan(_boundary(), _params())


@pytest.mark.asyncio
async def test_an_unexpected_status_is_an_outage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(CoveragePlannerUnavailable):
        await _adapter(handler).plan(_boundary(), _params())


@pytest.mark.asyncio
async def test_a_reply_missing_a_field_is_an_outage_not_a_silent_default():
    """A dropped metric must fail here rather than reach a mission as a default."""
    body = {**_PLAN_BODY, "metrics": {"swath_count": 4, "track_length_m": 52.9}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(CoveragePlannerUnavailable):
        await _adapter(handler).plan(_boundary(), _params())
