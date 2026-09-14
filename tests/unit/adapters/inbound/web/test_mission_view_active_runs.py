"""MissionView.active_runs: whether a mission is running, and which runs a command may name.

latest_run is the newest run created, whatever became of it. With concurrent runs allowed, the
newest can reach a terminal state while an older one still drives, so a reader deriving "is this
mission running" from latest_run alone reports a finished mission with a robot still moving.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.infrastructure.deps import (
    get_mission_repository,
    get_mission_run_repository,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository

_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


def _mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(mission: Mission, robot: str, status: RunStatus, created_at: datetime) -> MissionRun:
    return MissionRun(
        run_id=uuid4(),
        mission_id=mission.mission_id,
        robot_id=robot,
        status=status,
        stages=mission.stages,
        stages_digest=stages_digest(mission.stages),
        origin=RunOrigin(kind="manual", actor="human"),
        created_at=created_at,
        updated_at=created_at,
    )


def _app(missions, runs):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    app.dependency_overrides[get_mission_repository] = lambda: missions
    app.dependency_overrides[get_mission_run_repository] = lambda: runs
    return app


def _seed_concurrent_pair():
    """One run still driving, and a later one that has already been cancelled."""
    missions, runs = InMemoryMissionRepository(), InMemoryMissionRunRepository()
    mission = _mission()

    async def seed():
        await missions.save(mission)
        driving = await runs.create(_run(mission, "scout", RunStatus.RUNNING, _NOW))
        cancelled = await runs.create(
            _run(mission, "bonirob", RunStatus.CANCELLED, _NOW + timedelta(minutes=5))
        )
        return driving, cancelled

    driving, cancelled = asyncio.run(seed())
    return missions, runs, mission, driving, cancelled


def test_a_cancelled_later_run_does_not_make_the_mission_look_finished():
    missions, runs, mission, driving, cancelled = _seed_concurrent_pair()
    client = TestClient(_app(missions, runs))

    body = client.get(f"/api/v1/missions/{mission.mission_id}").json()

    assert body["latest_run"]["run_id"] == str(cancelled.run_id)
    assert body["latest_run"]["status"] == "CANCELLED"
    assert [r["run_id"] for r in body["active_runs"]] == [str(driving.run_id)]
    assert body["active_runs"][0]["robot_id"] == "scout"


def test_the_list_endpoint_carries_active_runs_too():
    missions, runs, mission, driving, _ = _seed_concurrent_pair()
    client = TestClient(_app(missions, runs))

    body = client.get("/api/v1/missions/").json()

    assert [r["run_id"] for r in body[0]["active_runs"]] == [str(driving.run_id)]


def test_a_mission_that_never_ran_has_no_active_runs():
    missions, runs = InMemoryMissionRepository(), InMemoryMissionRunRepository()
    mission = _mission()
    asyncio.run(missions.save(mission))
    client = TestClient(_app(missions, runs))

    body = client.get(f"/api/v1/missions/{mission.mission_id}").json()

    assert body["latest_run"] is None
    assert body["active_runs"] == []
