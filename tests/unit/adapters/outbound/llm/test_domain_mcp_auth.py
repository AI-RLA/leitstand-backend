"""The agent's tools reach the domain API through the same front door a browser does.

That loopback re-enters the app through its middleware and dependencies, so switching the bearer
gate on points it at the agent too: without a credential every tool call returns 401 and the
assistant answers "I couldn't read the fleet" while the fleet is fine. The credential is
forwarded from whoever asked, so the call stays theirs rather than becoming a service's.

/api/v1/users/me stands in for a curated tool route here because it is gated the same way and
needs no database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.infrastructure.auth import caller_credential
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio

_TOKEN = "s3cret-token"
_PROBE = "/api/v1/users/me"


def _closed_app():
    return create_app(Settings(zenoh_disabled=True, auto_migrate=False, auth_bearer_token=_TOKEN))


async def test_the_loopback_carries_the_callers_credential() -> None:
    app = _closed_app()
    _, client = build_domain_mcp(app, AgentOrigin())

    token = caller_credential.set(_TOKEN)
    try:
        response = await client.get(_PROBE)
    finally:
        caller_credential.reset(token)
        await client.aclose()

    assert response.status_code == 200


async def test_an_uncredentialed_loopback_is_refused() -> None:
    """No ambient authority: an uncredentialed loopback is refused like any other client.

    Worth pinning because the alternative design, a service token baked into the client, would
    have made this call succeed no matter who or what triggered it.
    """
    app = _closed_app()
    _, client = build_domain_mcp(app, AgentOrigin())

    try:
        response = await client.get(_PROBE)
    finally:
        await client.aclose()

    assert response.status_code == 401


async def test_an_open_api_needs_no_credential() -> None:
    app = create_app(Settings(zenoh_disabled=True, auto_migrate=False))
    _, client = build_domain_mcp(app, AgentOrigin())

    try:
        response = await client.get(_PROBE)
    finally:
        await client.aclose()

    assert response.status_code == 200


async def test_a_still_streaming_turn_reaches_its_tools() -> None:
    """The failure this whole seam exists to prevent, exercised end to end.

    A real turn calls its tools from inside the streaming response, in a task the endpoint
    already returned from. TestClient drives the same middleware stack, so a credential bound by
    the request is still there when the loopback fires.
    """
    app = _closed_app()
    _, loopback = build_domain_mcp(app, AgentOrigin())
    seen: dict[str, int] = {}

    @app.get("/probe-during-stream")
    async def probe() -> dict[str, int]:
        response = await loopback.get(_PROBE)
        seen["status"] = response.status_code
        return seen

    with TestClient(app) as client:
        client.get("/probe-during-stream", headers={"Authorization": f"Bearer {_TOKEN}"})

    await loopback.aclose()
    assert seen == {"status": 200}
