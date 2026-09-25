"""The model cannot know the date or hour, and "tomorrow" or "last night" are wrong without them."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.infrastructure.factory import create_app
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.settings import Settings


async def _instructions_for(zone: str) -> str:
    settings = Settings(zenoh_disabled=True, auto_migrate=False, chat_timezone=zone)
    domain_mcp, loopback = build_domain_mcp(create_app(settings), AgentOrigin())
    agent = build_chat_agent(settings, domain_mcp)
    seen: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return ModelResponse(parts=[TextPart("ok")])

    try:
        with agent.override(model=FunctionModel(model)):
            await agent.run("hi")
    finally:
        await loopback.aclose()
    return seen[0]


def _line(zone: str, moment: datetime) -> str:
    hours = f"between {moment:%H}:00 and {moment.hour + 1:02d}:00"
    return f"It is {moment:%A, %d %B %Y}, {hours} ({zone})."


@pytest.mark.asyncio
async def test_the_model_is_told_the_date_and_hour_in_the_operators_zone() -> None:
    """At any hour, one of these two zones is on a different date than UTC, so a UTC date fails."""
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
        before = datetime.now(ZoneInfo(zone))
        instructions = await _instructions_for(zone)
        after = datetime.now(ZoneInfo(zone))

        # Either side of a full hour, in case one passes while the turn runs.
        assert _line(zone, before) in instructions or _line(zone, after) in instructions


def test_an_unknown_timezone_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(zenoh_disabled=True, auto_migrate=False, chat_timezone="Mars/Olympus")
