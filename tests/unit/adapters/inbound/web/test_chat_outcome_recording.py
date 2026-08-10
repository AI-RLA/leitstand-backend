"""An approved action's outcome is recorded even when the turn dies after it ran.

The tool executes before the model request that ends the turn, so waiting for the run to finish
loses the outcome of an action that already happened. The row left behind reads approved with no
result, which is indistinguishable from one whose action never ran.

Driven through the endpoint, since the route's own wiring is what is under test.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastmcp import FastMCP
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.model.chat.tool_call import ToolCallStatus
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import get_tool_call_approval_service
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_CALL = "call_x"
_USER = User(id="operator", name="Operator")
_ARGS = {"mission_id": "m1", "robot_id": "r1"}


def _domain_mcp(*, tool_fails: bool) -> FastMCP:
    mcp = FastMCP(name="test-domain")

    @mcp.tool
    def dispatch_mission(mission_id: str, robot_id: str) -> str:
        if tool_fails:
            raise ModelRetry("the robot rejected the mission")
        return "dispatched"

    return mcp


def _dies_after_the_tool() -> FunctionModel:
    """A model that fails on the request following the tool, which is where the turn is lost."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise RuntimeError("model endpoint unreachable")
        yield ""  # unreachable, and what makes this an async generator

    return FunctionModel(stream_function=stream_function)


def _body() -> bytes:
    messages = [
        {"id": "m0", "role": "user", "parts": [{"type": "text", "text": "dispatch it"}]},
        {
            "id": "m1",
            "role": "assistant",
            "parts": [
                {
                    "type": "tool-leitstand_dispatch_mission",
                    "toolCallId": _CALL,
                    "state": "approval-responded",
                    "input": _ARGS,
                    "approval": {"id": _CALL, "approved": True},
                }
            ],
        },
    ]
    return json.dumps(
        {"id": "hapjMRcoYC0vHY0K", "trigger": "submit-message", "messages": messages}
    ).encode()


async def _decide_then_lose_the_turn(*, tool_fails: bool) -> InMemoryToolCallRepository:
    """Approve a pending call over the wire, run the turn, and let the model kill it afterwards."""
    settings = Settings(zenoh_disabled=True, auto_migrate=False, chat_enabled=True)
    repo = InMemoryToolCallRepository()
    service = ToolCallApprovalService(repo, _USER)
    await service.record_pending_tool_call(
        provider_tool_call_id=_CALL,
        tool_name="leitstand_dispatch_mission",
        arguments=_ARGS,
    )

    app = create_app(settings)
    app.dependency_overrides[get_tool_call_approval_service] = lambda: service

    # Lifespan assigns app.state, so the scripted agent is installed after it has run.
    async with app.router.lifespan_context(app):
        app.state.chat_agent = build_chat_agent(settings, _domain_mcp(tool_fails=tool_fails))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            with app.state.chat_agent.override(model=_dies_after_the_tool()):
                response = await client.post("/api/v1/chat", content=_body())
                assert response.status_code == 200

    # The premise, asserted rather than assumed: a turn that completed would record through the same
    # path and pass these tests without ever exercising the case they are named for.
    kinds = {
        json.loads(line[6:]).get("type")
        for line in response.text.splitlines()
        if line.startswith("data: {")
    }
    assert "error" in kinds
    return repo


async def test_a_success_is_recorded_when_the_turn_dies() -> None:
    repo = await _decide_then_lose_the_turn(tool_fails=False)

    (call,) = await repo.list_tool_calls_by_provider_id(str(_USER.id), _CALL)
    assert call.status is ToolCallStatus.SUCCEEDED
    assert call.result == {"content": "dispatched"}


async def test_a_failure_is_recorded_when_the_turn_dies() -> None:
    """The failure half: a tool that raises is the only shape a live tool failure takes."""
    repo = await _decide_then_lose_the_turn(tool_fails=True)

    (call,) = await repo.list_tool_calls_by_provider_id(str(_USER.id), _CALL)
    assert call.status is ToolCallStatus.FAILED
    assert call.error
