"""Unit tests for FieldManagementService using in-memory fakes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leitstand_backend.application.field_management_service import FieldManagementService
from leitstand_backend.domain.errors import FieldNotFoundError
from leitstand_backend.ports.inbound.field_management import (
    CreateFieldCommand,
    DeleteFieldCommand,
    UpdateFieldCommand,
)
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository

_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [
            [7.18, 50.785],
            [7.185, 50.785],
            [7.185, 50.788],
            [7.18, 50.788],
            [7.18, 50.785],
        ]
    ],
}


def _make_svc(repo=None):
    if repo is None:
        repo = InMemoryFieldRepository()
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

    svc = FieldManagementService(repo=repo, audit=audit)
    return svc, repo, audit_calls


@pytest.mark.asyncio
async def test_create_persists_field_and_audits() -> None:
    svc, repo, audit_calls = _make_svc()
    cmd = CreateFieldCommand(name="West Field", geometry=_POLYGON)

    field = await svc.create(cmd)

    assert field.name == "West Field"
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "field.create"
    assert await repo.get(field.id) == field


@pytest.mark.asyncio
async def test_update_returns_updated_field_and_audits() -> None:
    svc, _, audit_calls = _make_svc()
    field = await svc.create(CreateFieldCommand(name="East Field", geometry=_POLYGON))
    audit_calls.clear()

    updated = await svc.update(UpdateFieldCommand(field_id=field.id, notes="changed"))

    assert updated.notes == "changed"
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "field.update"
    assert audit_calls[0]["payload"]["patch"]["notes"] == "changed"
    assert "before" in audit_calls[0]["payload"]


@pytest.mark.asyncio
async def test_update_raises_when_not_found() -> None:
    svc, _, _ = _make_svc()
    with pytest.raises(FieldNotFoundError):
        await svc.update(UpdateFieldCommand(field_id=uuid4(), notes="x"))


@pytest.mark.asyncio
async def test_delete_audits_and_removes() -> None:
    svc, repo, audit_calls = _make_svc()
    field = await svc.create(CreateFieldCommand(name="South Field", geometry=_POLYGON))
    audit_calls.clear()

    await svc.delete(DeleteFieldCommand(field_id=field.id))

    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "field.delete"
    assert audit_calls[0]["payload"]["name"] == "South Field"
    assert await repo.get(field.id) is None


@pytest.mark.asyncio
async def test_delete_raises_when_not_found() -> None:
    svc, _, _ = _make_svc()
    with pytest.raises(FieldNotFoundError):
        await svc.delete(DeleteFieldCommand(field_id=uuid4()))
