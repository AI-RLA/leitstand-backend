"""A turn's tool calls must run under that turn's own identity, never a concurrent turn's.

The loopback reads the credential from context inside the MCP session's server task, so a session
shared across turns hands every overlapping turn the credential of whichever turn opened it.

Reproducing this requires ONE shared agent, exactly as the app holds on ``app.state.chat_agent``.
An agent per turn gives each its own session, so the bug disappears and these pass while the fault
is still present. The shared agent below is the condition under test, not an incidental detail.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.infrastructure.auth import caller_credential
from leitstand_backend.infrastructure.provenance import AgentOrigin, agent_origin
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio

_TOOL = "leitstand_list_robots"
_ORIGIN = AgentOrigin(model="nemotron", prompt_version="assistant-test")


def _probe_app(seen: list[tuple[str | None, str | None]]) -> FastAPI:
    """Build an app whose one curated read route reports the identity the loopback arrived with."""
    app = FastAPI()

    @app.get("/api/v1/robots", operation_id="list_robots")
    def list_robots() -> list:
        """List the fleet's robots."""
        origin = agent_origin.get()
        seen.append((caller_credential.get(), origin.model if origin else None))
        return []

    return app


def _turn(credential: str) -> None:
    """Bind one turn's credential, as the middleware does when the request arrives."""
    caller_credential.set(credential)


def _prompt_of(messages: list[ModelMessage]) -> str:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") == "user-prompt":
                return str(part.content)
    return ""


async def test_overlapping_turns_do_not_share_identity() -> None:
    """Two turns running at once must each reach the loopback as themselves.

    The handshake is two-way on purpose. Turn A signals only after its tool has already gone through,
    so its session is provably open while B calls, and B signals back before A finishes. A one-way
    wait would let B complete first, leaving the turns sequential and the test green against the very
    fault it exists to catch.
    """
    seen: list[tuple[str | None, str | None]] = []
    domain_mcp, loopback = build_domain_mcp(_probe_app(seen), _ORIGIN)
    a_called = asyncio.Event()
    b_done = asyncio.Event()
    started: set[str] = set()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        who = _prompt_of(messages)
        if who not in started:
            started.add(who)
            return ModelResponse(parts=[ToolCallPart(_TOOL, {})])
        if who == "a":
            a_called.set()
            await asyncio.wait_for(b_done.wait(), timeout=10)
        else:
            b_done.set()
        return ModelResponse(parts=[TextPart("done")])

    # One agent for both turns, exactly as the app holds one on app.state.chat_agent.
    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    async def turn_a() -> None:
        _turn("token-a")
        await agent.run("a")

    async def turn_b() -> None:
        await asyncio.wait_for(a_called.wait(), timeout=10)
        _turn("token-b")
        await agent.run("b")

    try:
        with agent.override(model=FunctionModel(model)):
            await asyncio.gather(turn_a(), turn_b())
    finally:
        await loopback.aclose()

    # The credential is what differs per turn; the origin is one process-wide fact, asserted here
    # so a turn that reaches the loopback without it cannot pass as isolated.
    assert seen == [("token-a", "nemotron"), ("token-b", "nemotron")]


async def test_a_tool_call_carries_the_credential() -> None:
    """Pin credential forwarding on the path tool calls actually take.

    The other loopback tests drive the httpx client directly, which runs in the caller's own task and
    so never opens an MCP session. That leaves the real route, agent to session to loopback, asserted
    nowhere, which is how a session-scoped identity fault stayed invisible.
    """
    seen: list[tuple[str | None, str | None]] = []
    domain_mcp, loopback = build_domain_mcp(_probe_app(seen), _ORIGIN)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(parts=[ToolCallPart(_TOOL, {})])

    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)
    _turn("token-solo")

    try:
        with agent.override(model=FunctionModel(model)):
            result = await agent.run("which robots are online?")
    finally:
        await loopback.aclose()

    assert seen == [("token-solo", "nemotron")]
    assert result.output == "done"
