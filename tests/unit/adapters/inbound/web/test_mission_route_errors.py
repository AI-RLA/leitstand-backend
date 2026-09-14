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
    AmbiguousRun,
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    FieldNotFoundError,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionArchived,
    MissionNotFoundError,
    MissionRunInProgress,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    RobotRefusedControl,
    RobotUnreachable,
    RunNotFoundError,
    StageNotInMission,
    StaleCoverageBoundary,
    UnknownSite,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.infrastructure.deps import (
    get_coverage_planning_use_case,
    get_mission_management_use_case,
    get_mission_repository,
    get_mission_run_repository,
    get_run_management_use_case,
    get_run_start_use_case,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository

_MISSION_ID = uuid4()
_STAGE_ID = uuid4()
_FIELD_ID = uuid4()

# Each error paired with the status the route must answer with. Constructed here rather than
# raised from the service, because the subject is the translation and not the rule.
_CASES = [
    (MissionNotFoundError(_MISSION_ID), 404),
    (InvalidMissionTransition(RunStatus.RUNNING, "assign"), 409),
    (RobotBusy("scout", _MISSION_ID), 409),
    (MissionArchived(_MISSION_ID), 409),
    (RobotFactsheetMissing("scout"), 422),
    (RobotPhysicalParametersMissing("scout"), 422),
    (UnsupportedStageKind("scout", _STAGE_ID, "coverage"), 422),
    (UnsupportedWaypointFrame("scout", _STAGE_ID, "site_local"), 422),
    (IncompatibleTurningRadius("scout", 1.5, 0.0), 422),
    (ImplementNarrowerThanRobot("scout", 4.0, 3.0), 422),
    (StaleCoverageBoundary(uuid4(), "has been edited"), 422),
]


# dispatch alone can also refuse for these.
_DISPATCH_ONLY_CASES = [
    (MissionRunInProgress(_MISSION_ID, [uuid4()]), 409),
    (NoRobotAssigned(_MISSION_ID), 422),
    # Freezing the run's site anchors is a dispatch-time read, so a site deleted since the
    # mission was written is reported here and nowhere else.
    (UnknownSite(uuid4()), 422),
]

# cancel / pause / resume resolve a run first, and can fail on that.
_STEER_CASES = [
    (MissionNotFoundError(_MISSION_ID), 404),
    (RunNotFoundError(uuid4()), 404),
    (AmbiguousRun(_MISSION_ID, [uuid4(), uuid4()]), 409),
    (InvalidMissionTransition(RunStatus.SUCCEEDED, "steer"), 409),
    (RobotUnreachable("scout", "pause"), 504),
    (RobotRefusedControl("scout", "pause", "estop latched"), 409),
]

# plan_coverage_mission raises a different set: the two below reach it through `replan`, which
# rewrites an existing mission and so can fail on that mission rather than on the plan.
_PLAN_CASES = [
    (MissionNotFoundError(_MISSION_ID), 404),
    (MissionArchived(_MISSION_ID), 409),
    (CoveragePlanRejected("the plan covers 40 m2 of a 170 m2 field"), 422),
    (FieldNotFoundError(_FIELD_ID), 404),
    (CoveragePlannerUnavailable("coverage planner unreachable"), 503),
    # replan names a stage; one the rewrite cannot reach is refused, not silently skipped.
    (StageNotInMission(_MISSION_ID, _STAGE_ID), 422),
]


def _app(error: Exception, *, on_dispatch: bool = False):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    class _Raises:
        async def assign(self, command):
            raise error

        async def start(self, command):
            raise error

        async def cancel(self, command):
            raise error

    app.dependency_overrides[get_mission_repository] = lambda: InMemoryMissionRepository()
    app.dependency_overrides[get_mission_run_repository] = lambda: InMemoryMissionRunRepository()
    app.dependency_overrides[
        get_run_start_use_case if on_dispatch else get_mission_management_use_case
    ] = lambda: _Raises()
    app.dependency_overrides[get_run_management_use_case] = lambda: _Raises()
    return app


@pytest.mark.parametrize("error,status", _CASES, ids=lambda v: type(v).__name__)
def test_assign_maps_each_domain_error_to_its_status(error, status):
    with TestClient(_app(error)) as client:
        resp = client.post(f"/api/v1/missions/{_MISSION_ID}/assign", json={"robot_id": "scout"})

    assert resp.status_code == status


@pytest.mark.parametrize(
    "error,status", _CASES + _DISPATCH_ONLY_CASES, ids=lambda v: type(v).__name__
)
def test_dispatch_maps_each_domain_error_to_its_status(error, status):
    with TestClient(_app(error, on_dispatch=True)) as client:
        resp = client.post(f"/api/v1/missions/{_MISSION_ID}/dispatch", json={"robot_id": "scout"})

    assert resp.status_code == status


@pytest.mark.parametrize("error,status", _STEER_CASES, ids=lambda v: type(v).__name__)
def test_cancel_maps_each_domain_error_to_its_status(error, status):
    with TestClient(_app(error)) as client:
        resp = client.post(f"/api/v1/missions/{_MISSION_ID}/cancel")

    assert resp.status_code == status


def test_reset_is_gone():
    with TestClient(_app(MissionNotFoundError(_MISSION_ID))) as client:
        assert client.post(f"/api/v1/missions/{_MISSION_ID}/reset").status_code == 410


def _plan_app(error: Exception):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    class _Raises:
        async def plan(self, command):
            raise error

    app.dependency_overrides[get_mission_repository] = lambda: InMemoryMissionRepository()
    app.dependency_overrides[get_mission_run_repository] = lambda: InMemoryMissionRunRepository()
    app.dependency_overrides[get_coverage_planning_use_case] = lambda: _Raises()
    return app


@pytest.mark.parametrize("error,status", _PLAN_CASES, ids=lambda v: type(v).__name__)
def test_plan_coverage_mission_maps_each_domain_error_to_its_status(error, status):
    """The replan flow can fail on the mission it rewrites, not only on the plan."""
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


_DELETE_RUN_CASES = [
    (RunNotFoundError(uuid4()), 404),
    (InvalidMissionTransition(RunStatus.RUNNING, "delete"), 409),
]


@pytest.mark.parametrize("error,status", _DELETE_RUN_CASES, ids=lambda v: type(v).__name__)
def test_delete_run_maps_each_domain_error_to_its_status(error, status):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    class _Raises:
        async def delete(self, command):
            raise error

    app.dependency_overrides[get_run_management_use_case] = lambda: _Raises()
    with TestClient(app) as client:
        assert client.delete(f"/api/v1/runs/{uuid4()}").status_code == status
