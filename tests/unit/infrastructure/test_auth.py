"""The bearer gate, and what it must not close over.

One shared token cannot express two identities, so these assert what it does carry: without it a
caller reaches nothing under /api/v1, with it everything the API exposes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from leitstand_backend.infrastructure.auth import (
    CredentialContextMiddleware,
    caller_credential,
    selected_subprotocol,
    token_accepted,
    ws_credential,
)
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings

_TOKEN = "s3cret-token"


def _app(**overrides):
    return create_app(Settings(zenoh_disabled=True, auto_migrate=False, **overrides))


def _closed():
    return _app(auth_bearer_token=_TOKEN)


def test_a_configured_token_is_required() -> None:
    with TestClient(_closed()) as client:
        assert client.get("/api/v1/users/me").status_code == 401


def test_the_right_token_is_let_through() -> None:
    with TestClient(_closed()) as client:
        response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert response.status_code == 200


def test_a_wrong_token_is_rejected() -> None:
    with TestClient(_closed()) as client:
        response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


def test_a_non_ascii_token_is_rejected() -> None:
    """Headers decode as latin-1, and compare_digest raises on a non-ASCII str.

    Asserted below the client, which refuses to send such a header at all.
    """
    settings = Settings(zenoh_disabled=True, auto_migrate=False, auth_bearer_token=_TOKEN)

    assert token_accepted(settings, "ü") is False


def test_a_token_in_the_wrong_scheme_is_not_accepted() -> None:
    """Basic auth carrying the right secret is still not a bearer."""
    with TestClient(_closed()) as client:
        response = client.get("/api/v1/users/me", headers={"Authorization": f"Basic {_TOKEN}"})

    assert response.status_code == 401


def test_no_configured_token_leaves_the_api_open() -> None:
    """The dev default, and how every other suite here runs."""
    with TestClient(_app()) as client:
        assert client.get("/api/v1/users/me").status_code == 200


def test_liveness_never_requires_a_token() -> None:
    """Probes hold no credential, so a 401 here is a restart loop."""
    with TestClient(_closed()) as client:
        assert client.get("/healthz").status_code == 200


def test_every_api_route_is_gated() -> None:
    """Auth is applied per router, so a router added later is one forgotten argument from open."""
    from leitstand_backend.infrastructure.deps import get_current_user

    def guards(route) -> bool:
        pending = list(route.dependant.dependencies)
        while pending:
            dependency = pending.pop()
            if dependency.call is get_current_user:
                return True
            pending.extend(dependency.dependencies)
        return False

    ungated = [
        route.path
        for route in _closed().routes
        if getattr(route, "path", "").startswith("/api/v1") and not guards(route)
    ]

    assert ungated == []


def test_the_credential_survives_into_the_stream() -> None:
    """A turn calls its tools while the response streams, long after the endpoint returned.

    If the credential were unbound by then the loopback would 401 on every tool call, and only a
    live model would show it.
    """
    app = FastAPI()
    app.add_middleware(CredentialContextMiddleware)

    @app.get("/streamed")
    def streamed() -> StreamingResponse:
        def body():
            # Read at iteration time, not at call time: that is the moment under test.
            yield (caller_credential.get() or "unbound").encode()

        return StreamingResponse(body())

    with TestClient(app) as client:
        response = client.get("/streamed", headers={"Authorization": f"Bearer {_TOKEN}"})

    assert response.text == _TOKEN


@pytest.mark.parametrize(
    ("offered", "expected"),
    [
        ([], None),
        (["leitstand.v1"], None),
        (["leitstand.v1", "leitstand.bearer.abc"], "abc"),
        (["leitstand.bearer."], None),
    ],
)
def test_ws_credential_parsing(offered, expected) -> None:
    assert ws_credential(offered) == expected


def test_the_token_subprotocol_is_never_echoed_back() -> None:
    """Echoing it would put the operator's token in anything that logs the handshake."""
    assert selected_subprotocol(["leitstand.v1", f"leitstand.bearer.{_TOKEN}"]) == "leitstand.v1"
    assert selected_subprotocol([f"leitstand.bearer.{_TOKEN}"]) is None
