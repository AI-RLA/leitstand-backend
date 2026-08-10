"""A turn that cannot converge is stopped from outside.

The model does not give up on its own: it cycles tool calls until something stops it. The
library's own ceiling is 50 model requests, which at this model's pace is minutes of a spinner.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimitExceeded

from leitstand_backend.adapters.outbound.llm.agent_factory import turn_usage_limits
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio


def _settings(**overrides) -> Settings:
    return Settings(zenoh_disabled=True, auto_migrate=False, **overrides)


def _never_settles(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Always call a tool, never answer."""
    return ModelResponse(parts=[ToolCallPart("look", {})])


def _answers_after_one_tool(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Call one tool, then answer."""
    called = any(
        part.part_kind == "tool-return" for m in messages for part in getattr(m, "parts", [])
    )
    if called:
        return ModelResponse(parts=[TextPart("scout_2_sim is online.")])
    return ModelResponse(parts=[ToolCallPart("look", {})])


def _agent(model: FunctionModel) -> Agent:
    agent = Agent(model)

    @agent.tool_plain
    def look() -> str:
        return "some fleet state"

    return agent


async def test_a_cycling_turn_is_cut_off() -> None:
    calls = 0

    def counting(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _never_settles(messages, info)

    agent = _agent(FunctionModel(counting))

    with pytest.raises(UsageLimitExceeded):
        await agent.run("go", usage_limits=turn_usage_limits(_settings(chat_max_tool_calls=4)))

    # Bounded by the cap, not by the library's default of 50.
    assert calls <= 6


async def test_an_ordinary_turn_is_unaffected() -> None:
    """The cap must not fire on a turn that settles."""
    agent = _agent(FunctionModel(_answers_after_one_tool))

    result = await agent.run(
        "which robots are online?", usage_limits=turn_usage_limits(_settings())
    )

    assert result.output == "scout_2_sim is online."


async def test_the_default_limits() -> None:
    limits = turn_usage_limits(_settings())

    assert limits.tool_calls_limit == 10
    assert limits.request_limit == 22
