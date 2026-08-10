"""The server's own record gates actuation, not the client's claim of approval.

The run would otherwise resume from the request body, so a stale or replayed approval could
actuate. These pin that only a decision this request actually settles is resumed: a replay, or a
call with no pending row, yields nothing to run.
"""

from __future__ import annotations

import json

import pytest
from pydantic_ai import ToolDenied

from leitstand_backend.adapters.inbound.web.chat import wire
from leitstand_backend.adapters.inbound.web.chat.routes import _settle_approvals
from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.user import User
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_USER = User(id="operator", name="Operator")


def _messages(provider_id: str, approved: bool):
    """The turn as the route sees it: parsed by the SDK, not hand-built dicts.

    Going through the real parse is what keeps these honest. A fixture the helper reads directly
    agrees with itself by construction, and would not notice the two disagreeing about what a field
    is called.
    """
    body = json.dumps(
        {
            "id": "chat-1",
            "trigger": "submit-message",
            "messages": [
                {"id": "m0", "role": "user", "parts": [{"type": "text", "text": "do it"}]},
                {
                    "id": "m1",
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool-leitstand_create_mission",
                            "toolCallId": provider_id,
                            "state": "approval-responded",
                            "input": {"name": "m"},
                            "approval": {"id": provider_id, "approved": approved},
                        }
                    ],
                },
            ],
        }
    ).encode()
    return wire.run_input(body).messages


async def _service_with_pending(provider_id: str) -> ToolCallApprovalService:
    service = ToolCallApprovalService(InMemoryToolCallRepository(), _USER)
    await service.record_pending_tool_call(
        provider_tool_call_id=provider_id,
        tool_name="leitstand_create_mission",
        arguments={"name": "m"},
    )
    return service


async def test_a_fresh_approval_is_resumed() -> None:
    service = await _service_with_pending("call_x")

    results = await _settle_approvals(service, _messages("call_x", True), _USER)

    assert results.approvals == {"call_x": True}


async def test_a_denial_resumes_as_denied_not_executed() -> None:
    service = await _service_with_pending("call_x")

    results = await _settle_approvals(service, _messages("call_x", False), _USER)

    assert isinstance(results.approvals["call_x"], ToolDenied)


async def test_a_replayed_approval_is_not_resumed_again() -> None:
    service = await _service_with_pending("call_x")

    first = await _settle_approvals(service, _messages("call_x", True), _USER)
    replay = await _settle_approvals(service, _messages("call_x", True), _USER)

    assert first.approvals == {"call_x": True}
    assert replay.approvals == {}  # already settled: nothing to resume, so it cannot run twice


async def test_an_approval_after_a_denial_is_not_resumed() -> None:
    """A decision made in one place must not be overturned by a claim from another."""
    service = await _service_with_pending("call_x")

    await _settle_approvals(service, _messages("call_x", False), _USER)
    conflicting = await _settle_approvals(service, _messages("call_x", True), _USER)

    assert conflicting.approvals == {}


async def test_an_approval_with_no_pending_row_is_not_resumed() -> None:
    """A resume for a call that was never recorded (e.g. an aborted pause) must not actuate."""
    service = await _service_with_pending("call_x")

    results = await _settle_approvals(service, _messages("call_unknown", True), _USER)

    assert results.approvals == {}
