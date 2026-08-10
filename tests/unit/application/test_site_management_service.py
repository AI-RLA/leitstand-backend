"""Unit tests for SiteManagementService using in-memory fakes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leitstand_backend.application.site_management_service import SiteManagementService
from leitstand_backend.domain.errors import SiteInUse, SiteNotFoundError
from leitstand_backend.ports.inbound.site_management import (
    CreateSiteCommand,
    DeleteSiteCommand,
    UpdateSiteCommand,
)
from tests.fakes.in_memory_site_repository import InMemorySiteRepository


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

    svc = SiteManagementService(repo=repo, audit=audit)
    return svc, repo, audit_calls


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
    svc, repo, audit_calls = _make_svc()
    site = await svc.create(_create_cmd())

    assert site.name == "dock_a"
    assert site.anchor_lat == 52.30
    assert audit_calls[0]["action"] == "site.create"
    assert await repo.get(site.site_id) == site


@pytest.mark.asyncio
async def test_update_returns_updated_site_and_audits():
    svc, _, audit_calls = _make_svc()
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
    svc, _, _ = _make_svc()
    with pytest.raises(SiteNotFoundError):
        await svc.update(UpdateSiteCommand(site_id=uuid4(), description="x"))


@pytest.mark.asyncio
async def test_delete_removes_site():
    svc, repo, audit_calls = _make_svc()
    site = await svc.create(_create_cmd())
    audit_calls.clear()

    await svc.delete(DeleteSiteCommand(site_id=site.site_id))

    assert audit_calls[0]["action"] == "site.delete"
    assert await repo.get(site.site_id) is None


@pytest.mark.asyncio
async def test_delete_raises_when_not_found():
    svc, _, _ = _make_svc()
    with pytest.raises(SiteNotFoundError):
        await svc.delete(DeleteSiteCommand(site_id=uuid4()))


@pytest.mark.asyncio
async def test_delete_raises_when_in_use():
    svc, repo, _ = _make_svc()
    site = await svc.create(_create_cmd())
    repo.mark_in_use(site.site_id)

    with pytest.raises(SiteInUse):
        await svc.delete(DeleteSiteCommand(site_id=site.site_id))
