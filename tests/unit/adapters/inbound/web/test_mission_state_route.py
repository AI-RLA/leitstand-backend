"""GET /missions/{id}/state -- per-stage runtime state with errors attributed per stage."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from leitstand_backend.domain.model.mission.mission import (
    Mission,
    MissionStatus,
    NavigationStage,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorReference,
    ErrorSeverity,
    MissionError,
)
from leitstand_backend.domain.model.mission.stage_state_record import StageStateRecord
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.infrastructure.deps import get_mission_repository
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository

_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def _mission_with_one_stage() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def _seed_failed_mission(repo: InMemoryMissionRepository, mission: Mission) -> None:
    stage_id = mission.stages[0].stage_id
    await repo.save(mission)
    error = MissionError(
        origin=ErrorOrigin.ROBOT,
        severity=ErrorSeverity.FATAL,
        type="nav2_unavailable",
        description="nav2 did not appear",
        references=[ErrorReference(key="stage_id", value=str(stage_id))],
    )
    await repo.update_status(mission.mission_id, MissionStatus.FAILED, errors=[error])
    await repo.overwrite_stage_states(
        mission.mission_id,
        [
            StageStateRecord(
                stage_id=stage_id, stage_index=0, status=StageStatus.FAILED, source_ts=_NOW
            )
        ],
    )


def _make_app(repo: InMemoryMissionRepository):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    app.dependency_overrides[get_mission_repository] = lambda: repo
    return app


def test_route_returns_per_stage_state_with_attributed_errors():
    repo = InMemoryMissionRepository()
    mission = _mission_with_one_stage()
    asyncio.run(_seed_failed_mission(repo, mission))
    client = TestClient(_make_app(repo))

    resp = client.get(f"/api/v1/missions/{mission.mission_id}/state")

    assert resp.status_code == 200
    stage = resp.json()["stage_states"][0]
    assert stage["status"] == "FAILED"
    assert stage["errors"][0]["type"] == "nav2_unavailable"
    assert stage["errors"][0]["origin"] == "robot"


def test_route_404_for_unknown_mission():
    client = TestClient(_make_app(InMemoryMissionRepository()))
    assert client.get(f"/api/v1/missions/{uuid4()}/state").status_code == 404
