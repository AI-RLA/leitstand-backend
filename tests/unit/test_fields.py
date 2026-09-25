"""Field CRUD + audit isolation unit tests using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from geojson_pydantic import Polygon

from leitstand_backend.application.field_management_service import FieldManagementService
from leitstand_backend.domain.model.field import Field
from leitstand_backend.infrastructure.deps import get_field_management_use_case
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
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
_BODY = {"name": "North field", "geometry": _POLYGON, "notes": "unit test"}


def _make_app(
    repo: InMemoryFieldRepository,
    audit_calls: list,
):
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))

    async def fake_audit(
        action: str,
        target_type: str | None,
        target_id: str | None,
        payload: dict | None,
    ) -> None:
        audit_calls.append(
            {
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            }
        )

    def override_uc():
        return FieldManagementService(repo=repo, audit=fake_audit)

    app.dependency_overrides[get_field_management_use_case] = override_uc
    return app


# ---------------------------------------------------------------------------
# Audit isolation tests
# ---------------------------------------------------------------------------


def test_create_writes_one_audit_row() -> None:
    repo = InMemoryFieldRepository()
    audit_calls: list = []
    app = _make_app(repo, audit_calls)

    with TestClient(app) as client:
        resp = client.post("/api/v1/fields/", json=_BODY)

    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "North field"
    assert len(audit_calls) == 1
    entry = audit_calls[0]
    assert entry["action"] == "field.create"
    assert entry["target_type"] == "field"
    assert entry["target_id"] == data["id"]
    assert entry["payload"]["name"] == "North field"


def test_create_no_audit_on_validation_failure() -> None:
    repo = InMemoryFieldRepository()
    audit_calls: list = []
    app = _make_app(repo, audit_calls)

    with TestClient(app) as client:
        bad = {"name": "Bad", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
        resp = client.post("/api/v1/fields/", json=bad)

    assert resp.status_code == 422
    assert len(audit_calls) == 0


def test_update_writes_audit_row() -> None:
    repo = InMemoryFieldRepository()
    audit_calls: list = []
    app = _make_app(repo, audit_calls)

    with TestClient(app) as client:
        field_id = client.post("/api/v1/fields/", json=_BODY).json()["id"]
        audit_calls.clear()
        resp = client.patch(f"/api/v1/fields/{field_id}", json={"notes": "updated"})

    assert resp.status_code == 200
    assert len(audit_calls) == 1
    assert audit_calls[0]["action"] == "field.update"
    assert audit_calls[0]["payload"]["patch"]["notes"] == "updated"
    assert "before" in audit_calls[0]["payload"]


def test_delete_audit_payload_contains_snapshot() -> None:
    repo = InMemoryFieldRepository()
    audit_calls: list = []
    app = _make_app(repo, audit_calls)

    with TestClient(app) as client:
        field_id = client.post("/api/v1/fields/", json=_BODY).json()["id"]
        audit_calls.clear()
        resp = client.delete(f"/api/v1/fields/{field_id}")

    assert resp.status_code == 204
    assert len(audit_calls) == 1
    entry = audit_calls[0]
    assert entry["action"] == "field.delete"
    assert entry["payload"]["name"] == "North field"
    assert entry["payload"]["geometry"]["type"] == "Polygon"


def test_update_404_no_audit_row() -> None:
    repo = InMemoryFieldRepository()
    audit_calls: list = []
    app = _make_app(repo, audit_calls)
    with TestClient(app) as client:
        resp = client.patch(f"/api/v1/fields/{uuid4()}", json={"notes": "x"})

    assert resp.status_code == 404
    assert len(audit_calls) == 0


# ---------------------------------------------------------------------------
# Center point
# ---------------------------------------------------------------------------


def test_a_field_carries_its_center() -> None:
    app = _make_app(InMemoryFieldRepository(), [])

    with TestClient(app) as client:
        data = client.post("/api/v1/fields/", json=_BODY).json()

    assert data["center_lat"] == pytest.approx(50.7865)
    assert data["center_lon"] == pytest.approx(7.1825)


def test_a_field_without_a_center_reports_null() -> None:
    repo = InMemoryFieldRepository()
    now = datetime.now(timezone.utc)
    field = Field(
        id=uuid4(),
        name="Empty",
        geometry=Polygon(type="Polygon", coordinates=[]),
        area_ha=None,
        created_at=now,
        updated_at=now,
    )
    repo.seed(field)
    app = _make_app(repo, [])

    with TestClient(app) as client:
        data = client.get(f"/api/v1/fields/{field.id}").json()

    assert data["center_lat"] is None
    assert data["center_lon"] is None


def test_a_position_outside_wgs84_is_refused() -> None:
    audit_calls: list = []
    app = _make_app(InMemoryFieldRepository(), audit_calls)
    ring = [[200.0, 50.0], [200.1, 50.0], [200.1, 50.1], [200.0, 50.0]]
    body = {"name": "Off the map", "geometry": {"type": "Polygon", "coordinates": [ring]}}

    with TestClient(app) as client:
        resp = client.post("/api/v1/fields/", json=body)

    assert resp.status_code == 422
    assert audit_calls == []
