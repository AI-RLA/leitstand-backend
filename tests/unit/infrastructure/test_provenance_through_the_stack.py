"""An agent write stays an agent write after crossing the app's own middleware stack.

The audit writer reads provenance ambiently, the loopback re-enters this app as an ordinary HTTP
request, and every middleware on the way in can set context variables. Nothing errors when one of
them clears the origin: the write simply records a human acting directly, which is the one direction
the record must never fail in. The pieces are asserted elsewhere; this composes them.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.outbound.llm.agent_factory import build_chat_agent
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.domain.model.audit import (
    ACTOR_AI_AGENT,
    ACTOR_HUMAN,
    AUTHORITY_APPROVED_PROPOSAL,
    AUTHORITY_AUTONOMOUS,
    AUTHORITY_DIRECT,
)
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.auth import CredentialContextMiddleware
from leitstand_backend.infrastructure.deps import _make_audit_writer
from leitstand_backend.infrastructure.middleware import RequestIdMiddleware
from leitstand_backend.infrastructure.provenance import AgentOrigin
from leitstand_backend.infrastructure.settings import Settings

pytestmark = pytest.mark.asyncio

_TOOL = "leitstand_list_robots"
_OPERATOR = User(id="operator", name="Operator")
_ORIGIN = AgentOrigin(model="nemotron", prompt_version="assistant-test")


class _CapturingSession:
    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)


def _probe_app(session: _CapturingSession) -> FastAPI:
    """Expose one read route that audits through the real writer, behind the real middleware.

    The middleware list mirrors the composition root's. One added there and not here leaves this
    blind to exactly the fault it exists to catch.
    """
    app = FastAPI()

    @app.get("/api/v1/robots", operation_id="list_robots")
    async def list_robots() -> list:
        """List the fleet's robots."""
        await _make_audit_writer(session, _OPERATOR)("robot.list", "robot", None, {})
        return []

    @app.post("/api/v1/missions/{mission_id}/dispatch", operation_id="dispatch_mission")
    async def dispatch_mission(mission_id: str) -> dict:
        """Send a mission to its assigned robot."""
        await _make_audit_writer(session, _OPERATOR)("mission.dispatch", "mission", mission_id, {})
        return {"mission_id": mission_id}

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(CredentialContextMiddleware)
    return app


def _one_tool_then_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if any(
        getattr(part, "part_kind", "") == "tool-return"
        for message in messages
        for part in getattr(message, "parts", [])
    ):
        return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart(_TOOL, {})])


async def test_a_loopback_write_is_audited_as_the_agent() -> None:
    """The origin survives the inbound middleware stack, so the row names the agent, not a human."""
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    try:
        with agent.override(model=FunctionModel(_one_tool_then_answer)):
            await agent.run("which robots are online?")
    finally:
        await loopback.aclose()

    row = session.rows[0]
    assert row.actor == ACTOR_AI_AGENT
    assert row.model == "nemotron"
    assert row.prompt_version == "assistant-test"
    # No approval settled this call, so it is the alarm rather than a human acting.
    assert row.authority == AUTHORITY_AUTONOMOUS
    assert row.decided_by is None


async def test_an_approved_write_names_its_approver() -> None:
    """The per-call approval reaches the audit row through the same stack the origin crosses.

    Without this the composed case would only ever assert the alarm, which an intact origin produces
    whether or not the approval channel works at all.
    """
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "leitstand_dispatch_mission", {"mission_id": "m-1"}, tool_call_id="call_1"
                )
            ]
        )

    decision_id = str(uuid4())
    try:
        with agent.override(model=FunctionModel(model)):
            paused = await agent.run("dispatch m-1")
            assert isinstance(paused.output, DeferredToolRequests)
            settled = DeferredToolResults(
                approvals={"call_1": True},
                metadata={"call_1": {"approved_by": "alice", "decision_id": decision_id}},
            )
            resumed = await agent.run(
                message_history=paused.all_messages(), deferred_tool_results=settled
            )
    finally:
        await loopback.aclose()

    assert resumed.output == "done"
    row = session.rows[0]
    assert row.actor == ACTOR_AI_AGENT
    assert row.authority == AUTHORITY_APPROVED_PROPOSAL
    assert row.decided_by == "alice"
    assert str(row.tool_call_id) == decision_id


async def test_a_direct_request_is_audited_as_human() -> None:
    """The negative control: without it, a design that labels everything agent would pass above."""
    import httpx

    session = _CapturingSession()
    app = _probe_app(session)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://browser"
    ) as client:
        assert (await client.get("/api/v1/robots")).status_code == 200

    row = session.rows[0]
    assert row.actor == ACTOR_HUMAN
    assert row.authority == AUTHORITY_DIRECT
    assert row.model is None
