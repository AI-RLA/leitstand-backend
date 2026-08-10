"""The WebSocket handshake is gated too.

The stream carries live pose, battery and mission state, so gating REST alone would hand an
unauthenticated client most of what the API protects.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings

_TOKEN = "s3cret-token"


def _closed():
    return create_app(Settings(zenoh_disabled=True, auto_migrate=False, auth_bearer_token=_TOKEN))


def test_an_unauthenticated_handshake_is_rejected() -> None:
    with TestClient(_closed()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/v1") as ws:
                ws.receive_json()


def test_a_wrong_token_is_rejected() -> None:
    with TestClient(_closed()) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/v1", subprotocols=["leitstand.v1", "leitstand.bearer.wrong"]
            ) as ws:
                ws.receive_json()


def test_a_token_in_the_handshake_connects() -> None:
    with TestClient(_closed()) as client:
        with client.websocket_connect(
            "/ws/v1", subprotocols=["leitstand.v1", f"leitstand.bearer.{_TOKEN}"]
        ) as ws:
            assert ws.receive_json().get("type") == "hello"


def test_no_configured_token_leaves_the_socket_open() -> None:
    """The dev default: no subprotocol offered, still connects."""
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    with TestClient(app) as client:
        with client.websocket_connect("/ws/v1") as ws:
            assert ws.receive_json().get("type") == "hello"
