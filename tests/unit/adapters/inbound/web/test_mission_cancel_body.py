"""POST /missions/{id}/cancel: the body's ``mode`` reaches the command, so IMMEDIATE is reachable."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from leitstand_backend.domain.errors import RunNotFoundError
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.infrastructure.deps import (
    get_mission_repository,
    get_mission_run_repository,
    get_run_management_use_case,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from leitstand_backend.ports.inbound.run_management import CancelRunCommand
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository

_NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


class _Recording:
    """Captures the command and refuses, so the route needs no run to render a view."""

    def __init__(self) -> None:
        self.commands: list[CancelRunCommand] = []

    async def cancel(self, command: CancelRunCommand):
        self.commands.append(command)
        raise RunNotFoundError(command.mission_id, mission_has_none=True)


def _client(uc: _Recording) -> tuple[TestClient, Mission]:
    missions = InMemoryMissionRepository()
    mission = Mission(
        mission_id=uuid4(),
        name="m",
        stages=[NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])],
        created_at=_NOW,
        updated_at=_NOW,
    )
    asyncio.run(missions.save(mission))
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    app.dependency_overrides[get_mission_repository] = lambda: missions
    app.dependency_overrides[get_mission_run_repository] = lambda: InMemoryMissionRunRepository()
    app.dependency_overrides[get_run_management_use_case] = lambda: uc
    return TestClient(app), mission


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, CancelMode.GRACEFUL),
        ({}, CancelMode.GRACEFUL),
        ({"mode": "graceful"}, CancelMode.GRACEFUL),
        ({"mode": "immediate"}, CancelMode.IMMEDIATE),
    ],
)
def test_the_bodys_mode_reaches_the_command(body, expected):
    uc = _Recording()
    client, mission = _client(uc)

    client.post(f"/api/v1/missions/{mission.mission_id}/cancel", json=body)

    assert [c.mode for c in uc.commands] == [expected]


def test_run_id_and_mode_travel_together():
    uc = _Recording()
    client, mission = _client(uc)
    run_id = uuid4()

    client.post(
        f"/api/v1/missions/{mission.mission_id}/cancel",
        json={"run_id": str(run_id), "mode": "immediate"},
    )

    assert (uc.commands[0].run_id, uc.commands[0].mode) == (run_id, CancelMode.IMMEDIATE)


def test_an_unknown_mode_is_a_422():
    client, mission = _client(_Recording())

    resp = client.post(f"/api/v1/missions/{mission.mission_id}/cancel", json={"mode": "now"})

    assert resp.status_code == 422
