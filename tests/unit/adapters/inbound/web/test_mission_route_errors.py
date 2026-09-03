"""Pin the status each domain error leaves the mission routes as.

The service suite proves these errors are raised; nothing proved what the wire did with them,
and an error absent from a route's except clause escapes as a 500 with the whole suite green.
That is not hypothetical: UnsupportedWaypointFrame reached this file that way.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from leitstand_backend.domain.errors import (
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    FieldNotFoundError,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionNotFoundError,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    StaleCoverageBoundary,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.infrastructure.deps import (
    get_coverage_planning_use_case,
    get_dispatch_use_case,
    get_mission_management_use_case,
    get_mission_repository,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository

_MISSION_ID = uuid4()
_STAGE_ID = uuid4()
_FIELD_ID = uuid4()

# Each error paired with the status the route must answer with. Constructed here rather than
# raised from the service, because the subject is the translation and not the rule.
_CASES = [
    (MissionNotFoundError(_MISSION_ID), 404),
    (InvalidMissionTransition("RUNNING", "assign"), 409),
    (RobotBusy("scout", _MISSION_ID), 409),
    (RobotFactsheetMissing("scout"), 422),
    (RobotPhysicalParametersMissing("scout"), 422),
    (UnsupportedStageKind("scout", _STAGE_ID, "coverage"), 422),
    (UnsupportedWaypointFrame("scout", _STAGE_ID, "site_local"), 422),
    (IncompatibleTurningRadius("scout", 1.5, 0.0), 422),
    (ImplementNarrowerThanRobot("scout", 4.0, 3.0), 422),
    (StaleCoverageBoundary(uuid4(), "has been edited"), 422),
]


# plan_coverage_mission raises a different set: the two below reach it through `replaces`, which
# supersedes an existing mission and so can fail on that mission rather than on the plan.
_PLAN_CASES = [
    (MissionNotFoundError(_MISSION_ID), 404),
    (InvalidMissionTransition("RUNNING", "delete"), 409),
    (CoveragePlanRejected("the plan covers 40 m2 of a 170 m2 field"), 422),
    (FieldNotFoundError(_FIELD_ID), 404),
    (CoveragePlannerUnavailable("coverage planner unreachable"), 503),
]


def _app(error: Exception, *, on_dispatch: bool = False):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    class _Raises:
        async def assign(self, command):
            raise error

        async def dispatch(self, command):
            raise error

    app.dependency_overrides[get_mission_repository] = lambda: InMemoryMissionRepository()
    app.dependency_overrides[
        get_dispatch_use_case if on_dispatch else get_mission_management_use_case
    ] = lambda: _Raises()
    return app


@pytest.mark.parametrize("error,status", _CASES, ids=lambda v: type(v).__name__)
def test_assign_maps_each_domain_error_to_its_status(error, status):
    with TestClient(_app(error)) as client:
        resp = client.post(f"/api/v1/missions/{_MISSION_ID}/assign", json={"robot_id": "scout"})

    assert resp.status_code == status


@pytest.mark.parametrize("error,status", _CASES, ids=lambda v: type(v).__name__)
def test_dispatch_maps_each_domain_error_to_its_status(error, status):
    with TestClient(_app(error, on_dispatch=True)) as client:
        resp = client.post(f"/api/v1/missions/{_MISSION_ID}/dispatch", json={"robot_id": "scout"})

    assert resp.status_code == status


def _plan_app(error: Exception):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    class _Raises:
        async def plan(self, command):
            raise error

    app.dependency_overrides[get_mission_repository] = lambda: InMemoryMissionRepository()
    app.dependency_overrides[get_coverage_planning_use_case] = lambda: _Raises()
    return app


@pytest.mark.parametrize("error,status", _PLAN_CASES, ids=lambda v: type(v).__name__)
def test_plan_coverage_mission_maps_each_domain_error_to_its_status(error, status):
    """The replaces flow can fail on the superseded mission, not only on the plan."""
    with TestClient(_plan_app(error)) as client:
        resp = client.post(
            "/api/v1/missions/coverage",
            json={
                "field_id": str(_FIELD_ID),
                "robot_id": "scout",
                "operation_width_m": 3.0,
            },
        )

    assert resp.status_code == status
