"""A tool call that is rejected or fails leaves a log line, because nothing else records it.

A call rejected before it reaches an approval card is not a stored proposal, so without the log the
only trace of it is the assistant's own apology.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastmcp import FastMCP
from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from structlog.testing import capture_logs

from leitstand_backend.adapters.inbound.web.chat.routes import _log_unsuccessful
from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import get_tool_call_approval_service
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_USER = User(id="operator", name="Operator")


def _domain_mcp() -> FastMCP:
    mcp = FastMCP(name="test-domain")

    @mcp.tool
    def list_robots() -> str:
        return "[]"

    return mcp


def _calls_an_unknown_tool_then_answers() -> FunctionModel:
    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[DeltaToolCalls | str]:
        if not any(isinstance(message, ModelResponse) for message in messages):
            yield {0: DeltaToolCall(name="list_robot", json_args="{}", tool_call_id="c1")}
        else:
            yield "No robots."

    return FunctionModel(stream_function=stream_function)


def _body() -> bytes:
    messages = [{"id": "m0", "role": "user", "parts": [{"type": "text", "text": "robots?"}]}]
    return json.dumps(
        {"id": "hapjMRcoYC0vHY0K", "trigger": "submit-message", "messages": messages}
    ).encode()


async def test_a_call_to_an_unknown_tool_is_logged_with_its_reason() -> None:
    settings = Settings(zenoh_disabled=True, auto_migrate=False, chat_enabled=True)
    service = ToolCallApprovalService(InMemoryToolCallRepository(), _USER)
    app = create_app(settings)
    app.dependency_overrides[get_tool_call_approval_service] = lambda: service

    # Lifespan assigns app.state, so the scripted agent is installed after it has run.
    async with app.router.lifespan_context(app):
        app.state.chat_agent = build_chat_agent(settings, _domain_mcp())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                app.state.chat_agent.override(model=_calls_an_unknown_tool_then_answers()),
                capture_logs() as logs,
            ):
                response = await client.post("/api/v1/chat", content=_body())

    assert response.status_code == 200
    (logged,) = [log for log in logs if log["event"] == "chat_tool_call_unsuccessful"]
    assert (logged["tool"], logged["outcome"]) == ("list_robot", "retry")
    assert "Unknown tool name: 'list_robot'" in logged["reason"]


async def test_a_refusal_passed_on_as_text_is_logged_cut_short() -> None:
    refusal = "Error calling tool 'create_field': HTTP error 422: " + "[8.02, 52.28], " * 400

    with capture_logs() as logs:
        _log_unsuccessful(RetryPromptPart(tool_name="create_field", content=refusal))

    assert logs[0]["reason"] == refusal[:300]


async def test_a_failed_result_is_logged_and_other_outcomes_are_not() -> None:
    with capture_logs() as logs:
        for outcome in ("failed", "success", "denied", "interrupted"):
            _log_unsuccessful(
                ToolReturnPart(tool_name="weather_now", content="upstream down", outcome=outcome)
            )

    assert [(log["event"], log["outcome"], log["reason"]) for log in logs] == [
        ("chat_tool_call_unsuccessful", "failed", "upstream down")
    ]
