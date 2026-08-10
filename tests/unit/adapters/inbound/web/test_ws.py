"""WebSocket hub smoke."""

from __future__ import annotations

from fastapi.testclient import TestClient

from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings


def test_ws_subscribe_receives_latched_snapshot() -> None:
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    with TestClient(app) as client:
        app.state.event_bus.publish(
            "events/robot/r1/pose",
            {"lat": 1.0, "lon": 2.0},
            latch=True,
        )
        with client.websocket_connect("/ws/v1") as ws:
            ws.send_json({"type": "subscribe", "topic": "events/robot/r1/pose"})
            for _ in range(3):
                msg = ws.receive_json()
                if msg.get("type") == "event":
                    assert msg["topic"] == "events/robot/r1/pose"
                    assert msg["payload"] == {"lat": 1.0, "lon": 2.0}
                    return
            raise AssertionError("did not receive snapshot event")


def test_ws_ping_returns_pong() -> None:
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1") as ws:
            assert ws.receive_json().get("type") == "hello"
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            assert msg.get("type") == "pong"


def test_ws_bad_frame_returns_error() -> None:
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1") as ws:
            assert ws.receive_json().get("type") == "hello"
            ws.send_json({"type": "nonsense"})
            msg = ws.receive_json()
            assert msg.get("type") == "error"
            assert msg.get("code") == "bad_frame"
