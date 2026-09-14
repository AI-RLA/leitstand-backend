"""Unit tests for SiteManagementService using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from leitstand_backend.application.site_management_service import SiteManagementService
from leitstand_backend.domain.errors import SiteInUse, SiteNotFoundError
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint
from leitstand_backend.ports.inbound.site_management import (
    CreateSiteCommand,
    DeleteSiteCommand,
    UpdateSiteCommand,
)
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_site_repository import InMemorySiteRepository

UTC_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _make_svc(repo: InMemorySiteRepository | None = None):
    if repo is None:
        repo = InMemorySiteRepository()
    audit_calls: list[dict] = []

    async def audit(action, target_type, target_id, payload):
        audit_calls.append(
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            }
        )

    missions = InMemoryMissionRepository()
    svc = SiteManagementService(repo=repo, missions=missions, audit=audit)
    return svc, repo, missions, audit_calls


def _create_cmd(name: str = "dock_a") -> CreateSiteCommand:
    return CreateSiteCommand(
        name=name,
        anchor_lat=52.30,
        anchor_lon=8.05,
        anchor_heading_deg=0.0,
        nav2_map_ref=name,
    )


@pytest.mark.asyncio
async def test_create_persists_site_and_audits():
    svc, repo, _, audit_calls = _make_svc()
    site = await svc.create(_create_cmd())

    assert site.name == "dock_a"
    assert site.anchor_lat == 52.30
    assert audit_calls[0]["action"] == "site.create"
    assert await repo.get(site.site_id) == site


@pytest.mark.asyncio
async def test_update_returns_updated_site_and_audits():
    svc, _, _, audit_calls = _make_svc()
    site = await svc.create(_create_cmd())
    audit_calls.clear()

    updated = await svc.update(
        UpdateSiteCommand(site_id=site.site_id, description="repaired loading bay")
    )

    assert updated.description == "repaired loading bay"
    assert audit_calls[0]["action"] == "site.update"
    assert "before" in audit_calls[0]["payload"]


@pytest.mark.asyncio
async def test_update_raises_when_not_found():
    svc, _, _, _ = _make_svc()
    with pytest.raises(SiteNotFoundError):
        await svc.update(UpdateSiteCommand(site_id=uuid4(), description="x"))


@pytest.mark.asyncio
async def test_delete_removes_site():
    svc, repo, _, audit_calls = _make_svc()
    site = await svc.create(_create_cmd())
    audit_calls.clear()

    await svc.delete(DeleteSiteCommand(site_id=site.site_id))

    assert audit_calls[0]["action"] == "site.delete"
    assert await repo.get(site.site_id) is None


@pytest.mark.asyncio
async def test_delete_raises_when_not_found():
    svc, _, _, _ = _make_svc()
    with pytest.raises(SiteNotFoundError):
        await svc.delete(DeleteSiteCommand(site_id=uuid4()))


@pytest.mark.asyncio
async def test_delete_raises_when_in_use():
    svc, repo, _, _ = _make_svc()
    site = await svc.create(_create_cmd())
    repo.mark_in_use(site.site_id)

    with pytest.raises(SiteInUse):
        await svc.delete(DeleteSiteCommand(site_id=site.site_id))


@pytest.mark.asyncio
async def test_a_site_any_mission_ever_drove_in_cannot_be_deleted():
    # Site-local positions are measured against this anchor, so a mission that named it keeps it
    # whether or not that mission is still current.
    svc, repo, missions, _ = _make_svc()
    site = await repo.create(
        name="Barn",
        anchor_lat=52.3,
        anchor_lon=8.05,
        anchor_heading_deg=90.0,
        nav2_map_ref="barn.yaml",
        outline=None,
        description=None,
    )
    mission = await missions.save(
        Mission(
            mission_id=uuid4(),
            name="indoors",
            stages=[
                NavigationStage(
                    stage_id=uuid4(),
                    waypoints=[SiteLocalWaypoint(site_id=site.site_id, x=1.0, y=2.0)],
                )
            ],
            created_at=UTC_NOW,
            updated_at=UTC_NOW,
        )
    )

    with pytest.raises(SiteInUse) as excinfo:
        await svc.delete(DeleteSiteCommand(site_id=site.site_id))

    assert excinfo.value.blocking_mission_ids == [mission.mission_id]
    assert await repo.get(site.site_id) is not None


@pytest.mark.asyncio
async def test_a_site_no_mission_names_is_deleted():
    svc, repo, _, _ = _make_svc()
    site = await repo.create(
        name="Unused",
        anchor_lat=52.3,
        anchor_lon=8.05,
        anchor_heading_deg=90.0,
        nav2_map_ref="unused.yaml",
        outline=None,
        description=None,
    )

    await svc.delete(DeleteSiteCommand(site_id=site.site_id))

    assert await repo.get(site.site_id) is None
