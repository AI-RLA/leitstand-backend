"""The approval gate must pause an actuating call, not run it.

The failure is quiet by construction: drop DeferredToolRequests from the output type, or apply the
wrapper on the wrong side of the prefix, and an actuating tool runs through with no error.

Exercised through build_chat_agent with only the model scripted, so this covers the real
composition rather than a re-creation.
"""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio


def _domain_mcp(executed: list[str]) -> FastMCP:
    """A stand-in domain server with one actuating tool and one read tool.

    Named exactly as the curated tools, because the gate keys on the bare operation id: the wrapper
    holds dispatch_mission in its actuating set and does not hold list_robots.
    """
    mcp = FastMCP(name="test-domain")

    @mcp.tool
    def dispatch_mission(mission_id: str, robot_id: str) -> str:
        executed.append("dispatch_mission")
        return "dispatched"

    @mcp.tool
    def list_robots() -> str:
        executed.append("list_robots")
        return "scout_2_sim online"

    return mcp


def _agent(executed: list[str]) -> Agent:
    return build_chat_agent(
        Settings(zenoh_disabled=True, auto_migrate=False), _domain_mcp(executed)
    )


def _calls(tool_name: str, args: dict) -> FunctionModel:
    """A model that proposes exactly one tool call and nothing else."""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    return FunctionModel(model)


def _reads_then_answers(tool_name: str, answer: str) -> FunctionModel:
    """A model that calls one tool, then answers once it sees the result: an ordinary read turn."""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        ran = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        )
        if ran:
            return ModelResponse(parts=[TextPart(answer)])
        return ModelResponse(parts=[ToolCallPart(tool_name, {})])

    return FunctionModel(model)


async def test_an_actuating_call_pauses_instead_of_executing() -> None:
    executed: list[str] = []
    agent = _agent(executed)

    with agent.override(
        model=_calls("leitstand_dispatch_mission", {"mission_id": "m1", "robot_id": "r1"})
    ):
        result = await agent.run("dispatch it")

    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_name for call in result.output.approvals] == ["leitstand_dispatch_mission"]
    assert executed == []  # the tool never ran; it is waiting on the operator's decision


async def test_a_read_call_runs_without_approval() -> None:
    """The gate is selective: a read tool must not pause, or every answer needs a click."""
    executed: list[str] = []
    agent = _agent(executed)

    with agent.override(
        model=_reads_then_answers("leitstand_list_robots", "scout_2_sim is online.")
    ):
        result = await agent.run("which robots are online?")

    assert result.output == "scout_2_sim is online."
    assert executed == ["list_robots"]
