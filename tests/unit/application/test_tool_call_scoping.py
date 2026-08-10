"""One operator cannot settle another operator's tool call.

With one user in production a scoping leak is unobservable, so these invent a second identity
rather than wait for auth. The owner is the only scope the approval lookup has.
"""

from __future__ import annotations

import pytest

from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.model.chat.tool_call import ToolCallStatus
from leitstand_backend.domain.user import User
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_VERB = "leitstand_dispatch_mission"
_ARGS = {"mission_id": "m-1"}


def _operators() -> tuple[
    ToolCallApprovalService, ToolCallApprovalService, InMemoryToolCallRepository
]:
    """Two operators sharing one store, as they would share one database."""
    repo = InMemoryToolCallRepository()
    return (
        ToolCallApprovalService(repo, User(id="alice", name="Alice")),
        ToolCallApprovalService(repo, User(id="bob", name="Bob")),
        repo,
    )


async def _pending(service: ToolCallApprovalService, provider_id: str) -> None:
    await service.record_pending_tool_call(
        provider_tool_call_id=provider_id,
        tool_name=_VERB,
        arguments=_ARGS,
    )


async def _decide(service: ToolCallApprovalService, provider_id: str, who: str):
    return await service.record_decision(
        provider_tool_call_id=provider_id,
        decided_by=who,
        approved=True,
        tool_name=_VERB,
        arguments=_ARGS,
    )


async def test_the_owner_settles_their_own_decision() -> None:
    alice, _, _ = _operators()
    await _pending(alice, "call_x")

    assert await _decide(alice, "call_x", "alice") is not None


async def test_another_operator_settles_nothing() -> None:
    alice, bob, _ = _operators()
    await _pending(alice, "call_x")

    assert await _decide(bob, "call_x", "bob") is None


async def test_a_shared_provider_id_keeps_rows_separate() -> None:
    """The model mints the id and knows nothing about who was asked, so two operators can share one."""
    alice, bob, repo = _operators()
    await _pending(alice, "call_x")
    await _pending(bob, "call_x")

    assert await _decide(bob, "call_x", "bob") is not None

    (alices,) = await repo.list_tool_calls_by_provider_id("alice", "call_x")
    (bobs,) = await repo.list_tool_calls_by_provider_id("bob", "call_x")
    assert alices.status is ToolCallStatus.AWAITING_APPROVAL
    assert bobs.status is ToolCallStatus.APPROVED
