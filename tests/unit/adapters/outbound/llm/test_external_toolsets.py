"""When an external MCP server fails, the turn goes on without its tools and still answers."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx2
import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.http import find_available_port
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai.response_types import SourceUrlChunk
from structlog.testing import capture_logs

from leitstand_backend.adapters.inbound.web.chat import wire
from leitstand_backend.adapters.outbound.llm.agent_factory import (
    _RunScopedMCPToolset,
    build_chat_agent,
)
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.adapters.outbound.llm.external_toolsets import (
    GuardedMCPToolset,
    SourceLinkToolset,
)
from leitstand_backend.infrastructure.external_mcp import ExternalMCPServer
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio

_SOURCE = {"title": "Weather data by Example", "url": "https://example.org/"}
_READ_ONLY = {"readOnlyHint": True}


class _Faulty(FunctionToolset):
    """A stand-in server whose failure mode a test switches between turns."""

    def __init__(self) -> None:
        super().__init__()
        self.mode = "ok"
        self.add_function(self.daily_forecast)

    def daily_forecast(self) -> str:
        """Forecast at a point."""
        return "sunny"

    async def __aenter__(self):
        if self.mode == "enter":
            raise httpx2.ConnectError("refused")
        return await super().__aenter__()

    async def __aexit__(self, *args: Any) -> bool | None:
        if self.mode == "call":
            raise httpx2.ConnectError("gone")
        return await super().__aexit__(*args)

    async def call_tool(self, *args: Any) -> Any:
        if self.mode == "call":
            raise httpx2.ConnectError("gone")
        if self.mode == "timeout":
            raise ModelRetry("Timed out while waiting for response")
        return await super().call_tool(*args)


def _guard(inner, allowed=("daily_forecast",)):
    guard = GuardedMCPToolset(inner, server="weather", allowed_tools=frozenset(allowed))
    return guard.prefixed("weather")


def _results(messages: list[ModelMessage]) -> list[Any]:
    return [
        part
        for message in messages
        for part in getattr(message, "parts", [])
        if part.part_kind in ("tool-return", "retry-prompt")
    ]


def _calls_twice_then_answers(seen: dict[str, Any]):
    """Call the weather tool twice when it is offered, then answer."""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["tools"] = [tool.name for tool in info.function_tools]
        seen["instructions"] = info.instructions or ""
        seen["results"] = _results(messages)
        if len(seen["results"]) < 2 and "weather_daily_forecast" in seen["tools"]:
            return ModelResponse(parts=[ToolCallPart("weather_daily_forecast", {})])
        return ModelResponse(parts=[TextPart("answered")])

    return model


async def _turn(toolset, seen: dict[str, Any] | None = None) -> str:
    seen = {} if seen is None else seen
    agent = Agent(FunctionModel(_calls_twice_then_answers(seen)), toolsets=[toolset], retries=1)
    return (await agent.run("weather?")).output


async def test_a_healthy_server_offers_its_tools_prefixed() -> None:
    seen: dict[str, Any] = {}

    assert await _turn(_guard(_Faulty()), seen) == "answered"
    assert seen["tools"] == ["weather_daily_forecast"]
    assert "unavailable" not in seen["instructions"]


async def test_a_server_down_at_turn_start_still_answers() -> None:
    inner = _Faulty()
    inner.mode = "enter"
    seen: dict[str, Any] = {}

    with capture_logs() as logs:
        assert await _turn(_guard(inner), seen) == "answered"

    assert seen["tools"] == []
    assert "The weather service is unavailable this turn" in seen["instructions"]
    assert "external_mcp_unavailable" in [log["event"] for log in logs]


@pytest.mark.parametrize("mode", ["call", "timeout"])
async def test_a_failure_mid_turn_still_answers(mode: str) -> None:
    inner = _Faulty()
    inner.mode = mode
    seen: dict[str, Any] = {}

    assert await _turn(_guard(inner), seen) == "answered"
    assert all("unavailable" in str(part.content) for part in seen["results"])


async def test_known_tools_are_offered_while_the_server_is_down() -> None:
    inner = _Faulty()
    toolset = _guard(inner)
    await _turn(toolset)

    inner.mode = "enter"
    seen: dict[str, Any] = {}

    assert await _turn(toolset, seen) == "answered"
    assert seen["tools"] == ["weather_daily_forecast"]
    assert all(part.part_kind == "tool-return" for part in seen["results"])


def _served(tool_error: bool = False) -> FastMCP:
    server = FastMCP("weather")

    @server.tool(meta={"source": _SOURCE}, annotations=_READ_ONLY)
    def daily_forecast() -> dict:
        """Forecast at a point."""
        if tool_error:
            raise ToolError("Open-Meteo did not answer in time.")
        return {"weather": ["Overcast"]}

    @server.tool(annotations=_READ_ONLY)
    def hidden() -> str:
        """A tool the configuration does not allow."""
        return "never"

    @server.tool
    def radar() -> str:
        """A tool that does not say it is read-only."""
        return "unmarked"

    return server


async def test_a_server_tool_error_reaches_the_model_unchanged() -> None:
    session = _RunScopedMCPToolset(_served(tool_error=True), tool_error_behavior="failed")
    seen: dict[str, Any] = {}

    assert await _turn(_guard(session), seen) == "answered"
    assert len(seen["results"]) == 2
    assert all("did not answer in time" in str(part.content) for part in seen["results"])


class _Recording(GuardedMCPToolset):
    """A guard that records every instance a turn enters."""

    entered: list[GuardedMCPToolset] = []

    async def __aenter__(self):
        _Recording.entered.append(self)
        return await super().__aenter__()


@pytest.mark.parametrize("renewed", [False, True])
async def test_each_turn_gets_its_own_guard_and_shares_the_memory(renewed: bool) -> None:
    """Overlapping turns must not share one guard's state, whatever the wrapped toolset does."""
    _Recording.entered.clear()
    inner = _RunScopedMCPToolset(_served(), tool_error_behavior="failed") if renewed else _Faulty()
    toolset = _Recording(
        inner, server="weather", allowed_tools=frozenset({"daily_forecast"})
    ).prefixed("weather")
    await _turn(toolset)
    await _turn(toolset)

    first, second = _Recording.entered
    assert first is not second
    assert first.memory is second.memory
    assert "daily_forecast" in second.memory.last_tools


async def test_the_source_reaches_the_stream_and_not_the_model() -> None:
    session = _RunScopedMCPToolset(_served(), tool_error_behavior="failed")
    agent = Agent(
        FunctionModel(_calls_twice_then_answers({})),
        toolsets=[_guard(SourceLinkToolset(session))],
        retries=1,
    )

    result = await agent.run("weather?")

    returns = [
        part
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 2 and all(isinstance(part.metadata, SourceUrlChunk) for part in returns)
    assert returns[0].metadata.url == _SOURCE["url"]
    assert returns[0].metadata.source_id != returns[1].metadata.source_id
    assert "example.org" not in returns[0].model_response_str()


async def test_a_history_with_a_source_entry_loads() -> None:
    body = {
        "id": "hapjMRcoYC0vHY0K",
        "trigger": "submit-message",
        "messages": [
            {"id": "m0", "role": "user", "parts": [{"type": "text", "text": "weather?"}]},
            {
                "id": "m1",
                "role": "assistant",
                "parts": [
                    {"type": "source-url", "sourceId": "c1", "url": _SOURCE["url"]},
                    {"type": "text", "text": "Overcast."},
                ],
            },
        ],
    }

    parsed = wire.run_input(json.dumps(body).encode())

    assert len(parsed.messages) == 2


async def test_a_missing_allowlisted_tool_is_logged_once() -> None:
    session = _RunScopedMCPToolset(_served(), tool_error_behavior="failed")
    toolset = _guard(session, allowed=("daily_forecast", "storms"))

    with capture_logs() as logs:
        await _turn(toolset)
        await _turn(toolset)

    missing = [log for log in logs if log["event"] == "external_mcp_tool_missing"]
    assert [log["tool"] for log in missing] == ["storms"]


@contextmanager
def _serving(server: FastMCP) -> Iterator[tuple[str, Callable[[], None]]]:
    """Serve over real HTTP on a thread in this process, stopped when the block ends."""
    port = find_available_port()
    app = server.http_app(stateless_http=True)
    running = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not running.started:
        assert time.monotonic() < deadline, "the test server did not start"
        time.sleep(0.05)

    def stop() -> None:
        running.should_exit = True
        thread.join(timeout=10)

    try:
        yield f"http://127.0.0.1:{port}/mcp", stop
    finally:
        stop()


async def test_only_allowed_read_only_tools_arrive_and_none_waits_for_approval() -> None:
    settings = Settings(zenoh_disabled=True, auto_migrate=False)
    domain_mcp, loopback = build_domain_mcp(create_app(settings), AgentOrigin())
    seen: dict[str, Any] = {}
    with _serving(_served()) as (url, _):
        external = {
            "weather": ExternalMCPServer(url=url, allowed_tools=["daily_forecast", "radar"])
        }
        agent = build_chat_agent(settings, domain_mcp, external)
        try:
            with agent.override(model=FunctionModel(_calls_twice_then_answers(seen))):
                result = await agent.run("weather?")
        finally:
            await loopback.aclose()

    assert not isinstance(result.output, DeferredToolRequests)
    weather = [name for name in seen["tools"] if name.startswith("weather_")]
    assert weather == ["weather_daily_forecast"]
    assert all(part.part_kind == "tool-return" for part in seen["results"])


async def test_a_real_server_that_stops_between_calls_leaves_the_turn_answering() -> None:
    with _serving(_served()) as (url, stop):
        session = _RunScopedMCPToolset(url, tool_error_behavior="failed")
        seen: dict[str, Any] = {}

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen["results"] = _results(messages)
            if len(seen["results"]) == 1:
                stop()
            if len(seen["results"]) < 2:
                return ModelResponse(parts=[ToolCallPart("weather_daily_forecast", {})])
            return ModelResponse(parts=[TextPart("answered")])

        agent = Agent(FunctionModel(model), toolsets=[_guard(session)], retries=1)

        assert (await agent.run("weather?")).output == "answered"

    assert "unavailable" in str(seen["results"][1].content)
