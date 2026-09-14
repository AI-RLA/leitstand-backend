"""GET /missions/{id}/state and GET /runs/{id}/state: per-stage state with errors attributed."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorReference,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.domain.model.mission.stage_status import StageStatus
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

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _mission_with_one_stage() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _seed_failed_run(
    missions: InMemoryMissionRepository, runs: InMemoryMissionRunRepository, mission: Mission
) -> MissionRun:
    stage_id = mission.stages[0].stage_id
    await missions.save(mission)
    run = await runs.create(
        MissionRun(
            run_id=uuid4(),
            mission_id=mission.mission_id,
            robot_id="scout",
            status=RunStatus.RUNNING,
            stages=mission.stages,
            stages_digest=stages_digest(mission.stages),
            origin=RunOrigin(kind="manual", actor="human"),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    error = MissionError(
        origin=ErrorOrigin.ROBOT,
        severity=ErrorSeverity.FATAL,
        type="nav2_unavailable",
        description="nav2 did not appear",
        references=[ErrorReference(key="stage_id", value=str(stage_id))],
    )
    await runs.update_status(
        run.run_id,
        RunStatus.FAILED,
        expected=RunStatus.RUNNING,
        trigger=RunTrigger.FAIL,
        errors=[error],
    )
    await runs.overwrite_stage_runs(
        run.run_id,
        [
            StageStateRecord(
                stage_id=stage_id, stage_index=0, status=StageStatus.FAILED, source_ts=_NOW
            )
        ],
    )
    return run


def _make_app(missions: InMemoryMissionRepository, runs: InMemoryMissionRunRepository):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    app.dependency_overrides[get_mission_repository] = lambda: missions
    app.dependency_overrides[get_mission_run_repository] = lambda: runs
    return app


def test_mission_state_returns_the_latest_runs_stages_with_attributed_errors():
    missions, runs = InMemoryMissionRepository(), InMemoryMissionRunRepository()
    mission = _mission_with_one_stage()
    run = asyncio.run(_seed_failed_run(missions, runs, mission))
    client = TestClient(_make_app(missions, runs))

    resp = client.get(f"/api/v1/missions/{mission.mission_id}/state")

    assert resp.status_code == 200
    assert resp.json()["run_id"] == str(run.run_id)
    stage = resp.json()["stage_states"][0]
    assert stage["status"] == "FAILED"
    assert stage["errors"][0]["type"] == "nav2_unavailable"
    assert stage["errors"][0]["origin"] == "robot"


def test_run_state_is_addressable_by_run_id():
    missions, runs = InMemoryMissionRepository(), InMemoryMissionRunRepository()
    mission = _mission_with_one_stage()
    run = asyncio.run(_seed_failed_run(missions, runs, mission))
    client = TestClient(_make_app(missions, runs))

    resp = client.get(f"/api/v1/runs/{run.run_id}/state")

    assert resp.status_code == 200
    assert resp.json()["mission_id"] == str(mission.mission_id)
    assert resp.json()["stage_states"][0]["status"] == "FAILED"


def test_a_mission_that_never_ran_has_no_state():
    missions, runs = InMemoryMissionRepository(), InMemoryMissionRunRepository()
    mission = _mission_with_one_stage()
    asyncio.run(missions.save(mission))
    client = TestClient(_make_app(missions, runs))
    assert client.get(f"/api/v1/missions/{mission.mission_id}/state").status_code == 404
    body = client.get(f"/api/v1/missions/{mission.mission_id}").json()
    assert body["latest_run"] is None


def test_route_404_for_unknown_mission_and_run():
    client = TestClient(_make_app(InMemoryMissionRepository(), InMemoryMissionRunRepository()))
    assert client.get(f"/api/v1/missions/{uuid4()}/state").status_code == 404
    assert client.get(f"/api/v1/runs/{uuid4()}/state").status_code == 404
    assert client.get(f"/api/v1/runs/{uuid4()}").status_code == 404
