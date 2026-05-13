"""Liveness and readiness probe tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from leitstand_backend.infrastructure.deps import ping_db
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings


async def _noop_ping_db() -> None:
    return


def _client() -> TestClient:
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    app.dependency_overrides[ping_db] = _noop_ping_db
    return TestClient(app)


def test_healthz_returns_ok() -> None:
    with _client() as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_readyz_ok_when_zenoh_disabled() -> None:
    """With zenoh_disabled=True the Zenoh check is skipped; DB ping succeeds."""
    with _client() as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}
