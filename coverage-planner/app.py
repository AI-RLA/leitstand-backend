"""Plans coverage paths over a field boundary, in WGS84.

Its own service because Fields2Cover is SWIG-wrapped C++: a native fault costs one planning
request rather than fleet control.
"""

from __future__ import annotations

import itertools
import math
import os
from typing import Any, Literal

import fields2cover as f2c
import pyproj
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

PLANNER_VERSION = os.environ.get("F2C_VERSION", "unknown")

app = FastAPI(title="leitstand coverage planner")


class Boundary(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class PlanRequest(BaseModel):
    # Python's JSON parser accepts Infinity, and these numbers go straight into C++.
    model_config = ConfigDict(allow_inf_nan=False)

    boundary: Boundary
    operation_width_m: float = Field(gt=0)
    turning_radius_m: float = Field(ge=0)
    headland_width_m: float = Field(ge=0)
    swath_angle_deg: float | None = Field(default=None, ge=0, lt=180)
    linear_curv_change: float = Field(gt=0)
    track_width_m: float = Field(gt=0)
    allow_overlap: bool = False
    # On the request rather than configured here: the caller owns the waypoint budget these
    # intervals spend.
    turn_sample_m: float = Field(gt=0)


class Waypoint(BaseModel):
    lat: float
    lon: float
    heading_deg: float | None = None


class Metrics(BaseModel):
    swath_count: int
    track_length_m: float = Field(
        description=(
            "Total swath length, which is the ground actually worked. Excludes the turns, so it "
            "is what the covered area is checked against rather than how far the machine drives."
        )
    )
    path_length_m: float = Field(
        ge=0, description="How far the machine drives in total, turns included."
    )
    covered_area_m2: float
    mainland_area_m2: float = Field(
        gt=0,
        description=(
            "Area left inside the headland, which is the ground the swaths were allowed to "
            "work. The field minus this is what the headland costs."
        ),
    )
    max_excursion_m: float = Field(
        ge=0,
        description=(
            "How far outside the boundary the driven path reaches, in metres. Turns need room "
            "the headland may not leave them, so a plan can be correct and still take the machine "
            "past the field edge. Reported because a boundary that is a fence makes that the "
            "difference between a manoeuvre and a collision."
        ),
    )


class Segment(BaseModel):
    """One swath across the field, or the turn joining two of them."""

    kind: Literal["swath", "turn"]
    waypoints: list[Waypoint]


class PlanResponse(BaseModel):
    segments: list[Segment] = Field(
        description=(
            "The whole plan as one ordered route. Concatenating the segments gives the drivable "
            "line; the kinds say which of it is a worked swath. Consecutive segments share an "
            "endpoint, which a consumer joining them skips."
        )
    )
    mainland_boundary: Boundary = Field(
        description=(
            "The ground the swaths were allowed to work: the field less its headland, as the "
            "planner cut it. Returned rather than left to be re-derived, because a consumer "
            "insetting the field itself would have to reproduce this offset exactly to avoid "
            "drawing swaths that cross their own limit."
        )
    )
    swath_angle_deg: float = Field(
        ge=0,
        lt=180,
        description=(
            "Bearing the swaths were laid out on. Echoed because the planner chooses one when the "
            "caller does not, and a plan can only be reproduced against the angle it truly used."
        ),
    )
    metrics: Metrics
    planner_version: str


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "planner_version": PLANNER_VERSION}


@app.post("/plan", response_model=PlanResponse)
def plan(request: PlanRequest) -> PlanResponse:
    ring = request.boundary.coordinates[0]
    if len(ring) < 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "boundary is not a closed ring")
    # Refused rather than ignored: planning the outer ring alone would drive straight through
    # whatever the inner rings were drawn to exclude, and the path would look correct.
    if len(request.boundary.coordinates) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"boundary has {len(request.boundary.coordinates) - 1} interior ring(s); "
            "planning around holes is not supported",
        )

    to_local, to_wgs84 = _projections(ring)
    projected = [to_local.transform(position[0], position[1]) for position in ring]

    cells = _cells(projected)
    field_area_m2 = cells.getArea()
    if field_area_m2 <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "boundary encloses no area")

    # Fields2Cover refuses an implement narrower than the machine, which would drive the wheels
    # over the strip just worked. Passing the working width for both would waive that.
    try:
        robot = f2c.Robot(request.track_width_m, request.operation_width_m)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    robot.setMinRadius(request.turning_radius_m)
    robot.linear_curv_change = request.linear_curv_change

    inner = f2c.HG_Const_gen().generateHeadlands(cells, request.headland_width_m)
    mainland_area_m2 = inner.getArea()
    if inner.size() == 0 or mainland_area_m2 <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"a {request.headland_width_m} m headland leaves nothing of a "
            f"{field_area_m2:.0f} m2 field to cover",
        )

    generator = f2c.SG_BruteForce()
    # A field is rarely a whole number of swaths wide. Overlapping the last pass covers the
    # remainder; refusing to leaves a strip the width of that remainder unworked.
    generator.setAllowOverlap(request.allow_overlap)
    mainland = inner.getGeometry(0)
    mainland_boundary = _boundary_out(mainland, to_wgs84)
    if request.swath_angle_deg is None:
        swaths = generator.generateBestSwaths(f2c.OBJ_NSwath(), request.operation_width_m, mainland)
    else:
        # F2C's swath angle and a compass bearing agree modulo the 180 degrees that separate
        # driving a line one way from the other, so the requested bearing passes straight through
        # and converting between the two conventions would turn the swaths a quarter circle.
        swaths = generator.generateSwaths(
            math.radians(request.swath_angle_deg), request.operation_width_m, mainland
        )
    if swaths.size() == 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"no swath {request.operation_width_m} m wide fits inside the field",
        )

    ordered = f2c.RP_Boustrophedon().genSortedSwaths(swaths)

    curve = f2c.PP_DubinsCurvesCC()
    # Left to its own default the turn generator emits roughly 0.004 m spacing, two orders of
    # magnitude finer than anything downstream reads.
    curve.discretization = request.turn_sample_m
    composed = f2c.PP_PathPlanning().searchBestPath(robot, ordered, curve)
    segments, metric_points = _segments(composed, to_wgs84)

    track_length_m = 0.0
    covered: object | None = None
    for index in range(ordered.size()):
        swath = ordered.at(index)
        track_length_m += swath.getLength()
        # Clipped to the field: the unclipped overload buffers the swath line with round end
        # caps, counting ground past each pass that no implement sweeps and that is not the
        # field's anyway.
        area = swath.computeAreaCovered(cells)
        # Union rather than sum, so overlapping swaths are not counted twice and the reported
        # area stays independent of track length, which the caller checks it against.
        covered = area if covered is None else covered.Union(area)

    covered_area_m2 = covered.getArea() if covered is not None else 0.0
    return PlanResponse(
        segments=segments,
        mainland_boundary=mainland_boundary,
        swath_angle_deg=_swath_bearing(ordered.at(0)),
        metrics=Metrics(
            swath_count=ordered.size(),
            track_length_m=track_length_m,
            covered_area_m2=covered_area_m2,
            mainland_area_m2=mainland_area_m2,
            path_length_m=_length(metric_points),
            max_excursion_m=_max_excursion_m(metric_points, projected),
        ),
        planner_version=PLANNER_VERSION,
    )


def _boundary_out(cell: Any, to_wgs84: pyproj.Transformer) -> Boundary:
    """Return a planner-frame cell's outer ring as a closed GeoJSON polygon."""
    ring = cell.getGeometry(0)
    points = [list(to_wgs84.transform(ring.getX(i), ring.getY(i))) for i in range(ring.size())]
    if points[0] != points[-1]:
        points.append(points[0])
    return Boundary(type="Polygon", coordinates=[points])


def _projections(ring: list[list[float]]) -> tuple[pyproj.Transformer, pyproj.Transformer]:
    """Return transformers to and from a metric frame centred on this field.

    Azimuthal equidistant about the field's own centre rather than a UTM zone: there is no zone to
    pick and therefore no zone to pick wrongly, and over a field-sized extent the distortion is far
    below the width of the machine.
    """
    lon0 = sum(position[0] for position in ring) / len(ring)
    lat0 = sum(position[1] for position in ring) / len(ring)
    local = pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
    )
    wgs84 = pyproj.CRS.from_epsg(4326)
    return (
        pyproj.Transformer.from_crs(wgs84, local, always_xy=True),
        pyproj.Transformer.from_crs(local, wgs84, always_xy=True),
    )


def _cells(projected: list[tuple[float, float]]) -> f2c.Cells:
    ring = f2c.LinearRing()
    for x, y in projected:
        ring.addPoint(f2c.Point(x, y))
    cell = f2c.Cell()
    cell.addRing(ring)
    return f2c.Cells(cell)


# Fields2Cover tags every state it emits, so which ground is worked is read from the planner
# rather than inferred from the coordinates afterwards.
_SEGMENT_KIND = {
    f2c.PathSectionType_SWATH: "swath",
    f2c.PathSectionType_TURN: "turn",
}


def _segments(
    composed: f2c.Path, to_wgs84: pyproj.Transformer
) -> tuple[list[Segment], list[tuple[float, float]]]:
    """Split the composed path into its swaths and the turns joining them.

    Returns the segments and the same route in the projected frame, which is what the length and
    excursion measurements work in.

    Fields2Cover repeats the point where one run ends and the next begins, so each segment carries
    its own copy of that junction and a consumer joining them drops the repeat.
    """
    # Snapshotted because the attribute builds a new proxy on each access.
    states = composed.states
    held = [states[index] for index in range(composed.size())]
    runs = [
        (_SEGMENT_KIND[section], list(run))
        for section, run in itertools.groupby(held, key=lambda state: state.type)
    ]

    route_states = list(runs[0][1])
    for _, states in runs[1:]:
        route_states.extend(states[1:])
    waypoints = _path_waypoints(route_states, to_wgs84)

    segments: list[Segment] = []
    offset = 0
    for kind, states in runs:
        segments.append(Segment(kind=kind, waypoints=waypoints[offset : offset + len(states)]))
        offset += len(states) - 1
    return segments, [(s.point.getX(), s.point.getY()) for s in route_states]


def _path_waypoints(states: list[Any], to_wgs84: pyproj.Transformer) -> list[Waypoint]:
    """Return the composed path with each point carrying the heading it is driven at.

    Taken from the direction Fields2Cover laid the point down at, not from the line to the next
    one: on a curve those differ by half the angle the step subtends, so a heading derived from
    the samples would change whenever the sampling did.
    """
    return [
        _waypoint((state.point.getX(), state.point.getY()), _compass(state.angle), to_wgs84)
        for state in states
    ]


def _compass(angle_rad: float) -> float:
    """Return a Fields2Cover heading as a compass bearing.

    The planner works in a frame with x east and y north, so an angle measured counterclockwise
    from east becomes a bearing measured clockwise from north.
    """
    bearing = (90.0 - math.degrees(angle_rad)) % 360.0
    # Python returns the modulus itself for a tiny negative, so due north approached from the west
    # yields 360.0, which is out of range everywhere downstream.
    return 0.0 if bearing >= 360.0 else bearing


def _length(points: list[tuple[float, float]]) -> float:
    """Return the total distance along a sequence of points, in metres."""
    return sum(math.dist(start, end) for start, end in zip(points, points[1:]))


def _swath_bearing(swath: f2c.Swath) -> float:
    """Return the compass bearing a swath is laid out on, modulo the 180 degrees of its direction.

    Measured from the swath's own endpoints rather than read from Fields2Cover, so it is expressed
    in the same convention as every heading this service emits.
    """
    path = swath.getPath()
    start = (path.getX(0), path.getY(0))
    end = (path.getX(path.size() - 1), path.getY(path.size() - 1))
    bearing = _bearing(start, end) % 180.0
    return 0.0 if bearing >= 180.0 else bearing


def _max_excursion_m(
    points: list[tuple[float, float]], boundary: list[tuple[float, float]]
) -> float:
    """Return how far the furthest point of the path lies outside the boundary, in metres.

    Zero when the path stays inside. Turns need a headland at least as deep as the turning radius
    to be contained, so a correct plan routinely leaves the polygon; whether that matters depends
    on what the boundary is made of, which only the operator knows.
    """
    worst = 0.0
    for point in points:
        if _inside(point, boundary):
            continue
        worst = max(worst, min(_distance_to_segment(point, a, b) for a, b in _edges(boundary)))
    return worst


def _edges(
    boundary: list[tuple[float, float]],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return list(zip(boundary, boundary[1:] + boundary[:1]))


def _inside(point: tuple[float, float], boundary: list[tuple[float, float]]) -> bool:
    """Return whether the point lies within the boundary ring, by crossing count."""
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in _edges(boundary):
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def _distance_to_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0.0:
        return math.hypot(px - ax, py - ay)
    along = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + along * dx), py - (ay + along * dy))


def _waypoint(
    point: tuple[float, float],
    heading_deg: float,
    to_wgs84: pyproj.Transformer,
) -> Waypoint:
    lon, lat = to_wgs84.transform(point[0], point[1])
    return Waypoint(lat=lat, lon=lon, heading_deg=heading_deg)


def _bearing(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Return the compass bearing from ``start`` to ``end``.

    The projected frame has x east and y north, so a mathematical angle measured counterclockwise
    from east becomes a bearing measured clockwise from north.
    """
    bearing = (90.0 - math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))) % 360.0
    # Python returns the modulus itself for a tiny negative, so due north approached from the west
    # yields 360.0, which is out of range everywhere downstream.
    return 0.0 if bearing >= 360.0 else bearing
