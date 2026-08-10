"""An audited write names the approval that authorised it, and only that one.

Authority is observed per call, not inferred from the turn: a turn can settle two approvals at once,
and inferring would stamp one approver across both writes.

Every link in the chain fails silently. The approval facts leave the run as request metadata, cross
into the MCP server's own task, and are read back ambiently at the audit writer, so a broken link
writes the row as an agent acting alone rather than raising. These drive the whole chain through a
real loopback for that reason, rather than asserting its pieces.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from leitstand_backend.adapters.inbound.web.chat import wire
from leitstand_backend.adapters.inbound.web.chat.routes import _settle_approvals
from leitstand_backend.adapters.outbound.llm.agent_factory import (
    _forward_call_provenance,
    _RunScopedMCPToolset,
    build_chat_agent,
)
from leitstand_backend.adapters.outbound.llm.domain_mcp import build_domain_mcp
from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.model.audit import (
    AUTHORITY_APPROVED_PROPOSAL,
    AUTHORITY_AUTONOMOUS,
)
from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import _make_audit_writer
from leitstand_backend.infrastructure.provenance import (
    AgentOrigin,
    tool_call_provenance,
)
from leitstand_backend.infrastructure.settings import Settings
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_DISPATCH = "leitstand_dispatch_mission"
_LIST = "leitstand_list_robots"
_OPERATOR = User(id="operator", name="Operator")
_BAD_MISSION = "invalid"


class _CapturingSession:
    """Collects the rows the audit writer adds, standing in for a real AsyncSession."""

    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)


_ORIGIN = AgentOrigin(model="nemotron", prompt_version="assistant-test")


@pytest.fixture(autouse=True)
def _clear_call_provenance():
    """Start each test with no approval ambient, so a stamp can only come from the run itself."""
    call = tool_call_provenance.set(None)
    yield
    tool_call_provenance.reset(call)


def _probe_app(session: _CapturingSession) -> FastAPI:
    """Expose one actuating and one read route, each auditing the write it performs.

    The routes audit through the real writer so what is asserted is the row an operator would later
    read, not an intermediate value that happens to be in flight.
    """
    app = FastAPI()

    @app.post("/api/v1/missions/{mission_id}/dispatch", operation_id="dispatch_mission")
    async def dispatch_mission(mission_id: str) -> dict:
        """Send a mission to its assigned robot."""
        if mission_id == _BAD_MISSION:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "stages", 0, "waypoints", 0],
                        "msg": "Unable to extract tag using discriminator 'kind'",
                    }
                ],
            )
        await _make_audit_writer(session, _OPERATOR)("mission.dispatch", "mission", mission_id, {})
        return {"mission_id": mission_id}

    @app.get("/api/v1/robots", operation_id="list_robots")
    async def list_robots() -> list:
        """List the fleet's robots."""
        await _make_audit_writer(session, _OPERATOR)("robot.list", "robot", None, {})
        return []

    return app


def _approve(requests: DeferredToolRequests, approvers: dict[str, str]) -> DeferredToolResults:
    """Settle every paused call, attaching the decision that settled it.

    Mirrors what the chat route attaches once its compare-and-set has settled a decision: the
    approver and the id of the ``tool_calls`` row holding the recorded proposal.
    """
    approvals: dict[str, Any] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for part in requests.approvals:
        approvals[part.tool_call_id] = True
        metadata[part.tool_call_id] = {
            "approved_by": approvers[str(part.args_as_dict()["mission_id"])],
            "decision_id": str(uuid4()),
        }
    return DeferredToolResults(approvals=approvals, metadata=metadata)


def _rows_by_target(session: _CapturingSession) -> dict[str | None, Any]:
    return {row.target_id: row for row in session.rows}


def _has_tool_return(messages: list[ModelMessage]) -> bool:
    return any(
        getattr(part, "part_kind", "") == "tool-return"
        for message in messages
        for part in getattr(message, "parts", [])
    )


async def test_the_rebuild_carries_the_configuration() -> None:
    """The toolset rebuilds per run and drops whatever the rebuild does not copy.

    Losing a field raises nothing; only the audit row is poorer. Every field is asserted because
    the rebuild is an allowlist, so one added upstream is absent until someone adds it here too.
    """
    domain_mcp, loopback = build_domain_mcp(_probe_app(_CapturingSession()), _ORIGIN)
    toolset = _RunScopedMCPToolset(domain_mcp, process_tool_call=_forward_call_provenance)
    toolset.tool_error_behavior = "raise"
    toolset.max_retries = 3
    toolset.cache_tools = True
    toolset.cache_resources = True
    toolset.cache_prompts = True

    try:
        fresh = await toolset.for_run(None)
    finally:
        await loopback.aclose()

    assert fresh.process_tool_call is _forward_call_provenance
    assert fresh.tool_error_behavior == "raise"
    assert fresh.max_retries == 3
    assert (fresh.cache_tools, fresh.cache_resources, fresh.cache_prompts) == (True, True, True)


async def test_two_approvals_get_separate_decisions() -> None:
    """Each write names the decision that authorised it, not one label shared across the turn.

    Ordinary operation, not an edge case: the client sends both decisions in one request whenever
    a turn proposes two actions.
    """
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    approvers = {"m-1": "alice", "m-2": "bob"}

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart(_DISPATCH, {"mission_id": "m-1"}, tool_call_id="call_1"),
                ToolCallPart(_DISPATCH, {"mission_id": "m-2"}, tool_call_id="call_2"),
            ]
        )

    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    try:
        with agent.override(model=FunctionModel(model)):
            paused = await agent.run("dispatch both")
            assert isinstance(paused.output, DeferredToolRequests)
            settled = _approve(paused.output, approvers)
            resumed = await agent.run(
                message_history=paused.all_messages(), deferred_tool_results=settled
            )
    finally:
        await loopback.aclose()

    # The turn has to have finished: a run that paused again would write no rows at all, and the
    # per-row assertions below would then be vacuously true over an empty set.
    assert resumed.output == "done"
    rows = _rows_by_target(session)
    assert set(rows) == {"m-1", "m-2"}
    for mission_id, approver in approvers.items():
        row = rows[mission_id]
        assert row.actor == "ai_agent"
        assert row.authority == AUTHORITY_APPROVED_PROPOSAL
        assert row.decided_by == approver
        assert row.tool_call_id is not None
    assert rows["m-1"].tool_call_id != rows["m-2"].tool_call_id


async def test_an_unapproved_call_stays_the_alarm() -> None:
    """An unapproved call in a turn that settled an approval must still read as unapproved.

    This is what separates authority observed per call from authority inferred per turn: inferring
    marks every write in the turn as approved, including one no operator ever saw. The alarm only
    means anything if it survives sitting next to a genuine approval.
    """
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    calls: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not calls:
            calls.append("dispatch")
            return ModelResponse(
                parts=[ToolCallPart(_DISPATCH, {"mission_id": "m-1"}, tool_call_id="call_1")]
            )
        if len(calls) == 1:
            calls.append("list")
            return ModelResponse(parts=[ToolCallPart(_LIST, {}, tool_call_id="call_2")])
        return ModelResponse(parts=[TextPart("done")])

    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    try:
        with agent.override(model=FunctionModel(model)):
            paused = await agent.run("dispatch m-1 then list robots")
            assert isinstance(paused.output, DeferredToolRequests)
            await agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=_approve(paused.output, {"m-1": "alice"}),
            )
    finally:
        await loopback.aclose()

    assert len(session.rows) == 2
    rows = _rows_by_target(session)
    assert rows["m-1"].authority == AUTHORITY_APPROVED_PROPOSAL
    unapproved = rows[None]
    assert unapproved.authority == AUTHORITY_AUTONOMOUS
    assert unapproved.decided_by is None
    assert unapproved.tool_call_id is None


async def test_the_routes_own_settle_reaches_the_audit_row() -> None:
    """Span the seam the other cases skip: the metadata the route mints, not one written here.

    Everything above builds its own decision metadata, so the names the route actually uses are
    never exercised. Those names are read again in two other modules, and a disagreement between
    any of them raises nothing: the write is simply audited as an agent that acted with nobody's
    approval. Making a genuine approval look like the alarm is the worst direction this chain can
    fail in, so the route's own spelling of it is driven here.
    """
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)
    service = ToolCallApprovalService(InMemoryToolCallRepository(), _OPERATOR)
    await service.record_pending_tool_call(
        provider_tool_call_id="call_1",
        tool_name=_DISPATCH,
        arguments={"mission_id": "m-1"},
    )
    body = json.dumps(
        {
            "id": "chat-1",
            "trigger": "submit-message",
            "messages": [
                {"id": "m0", "role": "user", "parts": [{"type": "text", "text": "dispatch m-1"}]},
                {
                    "id": "m1",
                    "role": "assistant",
                    "parts": [
                        {
                            "type": f"tool-{_DISPATCH}",
                            "toolCallId": "call_1",
                            "state": "approval-responded",
                            "input": {"mission_id": "m-1"},
                            "approval": {"id": "call_1", "approved": True},
                        }
                    ],
                },
            ],
        }
    ).encode()

    settled = await _settle_approvals(service, wire.run_input(body).messages, _OPERATOR)
    assert settled.approvals, "precondition: the route's settle must have moved the row"

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _has_tool_return(messages):
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart(_DISPATCH, {"mission_id": "m-1"}, tool_call_id="call_1")]
        )

    try:
        with agent.override(model=FunctionModel(model)):
            paused = await agent.run("dispatch m-1")
            assert isinstance(paused.output, DeferredToolRequests)
            await agent.run(message_history=paused.all_messages(), deferred_tool_results=settled)
    finally:
        await loopback.aclose()

    (row,) = session.rows
    assert row.actor == "ai_agent"
    assert row.authority == AUTHORITY_APPROVED_PROPOSAL
    assert row.decided_by == str(_OPERATOR.id)
    assert row.tool_call_id is not None


async def test_a_rejected_call_names_the_wrong_field() -> None:
    """A validation body is unreadable to the model, and it retries the same call rather than fix it.

    Left unreadable, the error costs a round of identical failures until the turn cap stops it.
    Naming the field and the problem is what makes the error actionable.
    """
    session = _CapturingSession()
    domain_mcp, loopback = build_domain_mcp(_probe_app(session), _ORIGIN)
    seen_by_model: list[str] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                if getattr(part, "part_kind", "") in {"retry-prompt", "tool-return"}:
                    seen_by_model.append(str(getattr(part, "content", "")))
        if seen_by_model:
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[ToolCallPart(_DISPATCH, {"mission_id": _BAD_MISSION}, tool_call_id="call_1")]
        )

    agent = build_chat_agent(Settings(zenoh_disabled=True, auto_migrate=False), domain_mcp)

    try:
        with agent.override(model=FunctionModel(model)):
            paused = await agent.run("dispatch the broken one")
            assert isinstance(paused.output, DeferredToolRequests)
            await agent.run(
                message_history=paused.all_messages(),
                deferred_tool_results=_approve(paused.output, {_BAD_MISSION: "alice"}),
            )
    finally:
        await loopback.aclose()

    reported = " ".join(seen_by_model)
    assert "stages.0.waypoints.0" in reported
    assert "discriminator" in reported
    assert '"loc"' not in reported
