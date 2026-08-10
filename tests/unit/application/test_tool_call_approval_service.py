"""A proposed actuation is recorded when it pauses, and one decision settles it once.

The twin-row cases drive the rule that picks which proposal a decision answers. They need two rows
under one provider id, which a model minting weak ids produces routinely, so they seed the store
directly to fix the ages the rule reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.application.tool_call_approval_service import ToolCallApprovalService
from leitstand_backend.domain.model.chat.tool_call import ToolCall, ToolCallStatus
from leitstand_backend.domain.user import User
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_USER = "operator"
_VERB = "leitstand_dispatch_mission"
_ARGS = {"mission_id": "m-1"}
_T0 = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


def _service(repo: InMemoryToolCallRepository) -> ToolCallApprovalService:
    return ToolCallApprovalService(repo, User(id=_USER, name="Operator"))


async def _service_with_pending() -> tuple[
    ToolCallApprovalService, InMemoryToolCallRepository, str
]:
    """A service holding one pending actuating tool call."""
    repo = InMemoryToolCallRepository()
    service = _service(repo)
    await service.record_pending_tool_call(
        provider_tool_call_id="call_x",
        tool_name=_VERB,
        arguments=_ARGS,
    )
    return service, repo, "call_x"


async def _decide(
    service: ToolCallApprovalService,
    provider_id: str,
    *,
    approved: bool = True,
    tool_name: str = _VERB,
    arguments: dict | None = None,
) -> UUID | None:
    return await service.record_decision(
        provider_tool_call_id=provider_id,
        decided_by=_USER,
        approved=approved,
        tool_name=tool_name,
        arguments=_ARGS if arguments is None else arguments,
    )


async def _seed(
    repo: InMemoryToolCallRepository,
    *,
    provider_id: str,
    arguments: dict,
    status: ToolCallStatus,
    at: datetime,
) -> ToolCall:
    """Put one proposal in the store with an exact age, so the ordering under test is fixed."""
    call = ToolCall(
        id=uuid4(),
        user_id=_USER,
        provider_tool_call_id=provider_id,
        tool_name=_VERB,
        arguments=arguments,
        status=status,
        created_at=at,
    )
    await repo.add_tool_call(call)
    return call


async def _row(repo: InMemoryToolCallRepository, provider_id: str, tool_call_id: UUID) -> ToolCall:
    rows = await repo.list_tool_calls_by_provider_id(_USER, provider_id)
    return next(row for row in rows if row.id == tool_call_id)


async def _newest(repo: InMemoryToolCallRepository, provider_id: str) -> ToolCall:
    return (await repo.list_tool_calls_by_provider_id(_USER, provider_id))[0]


async def test_a_pause_is_recorded_awaiting_approval() -> None:
    _, repo, provider_id = await _service_with_pending()

    call = await _newest(repo, provider_id)
    assert call.status is ToolCallStatus.AWAITING_APPROVAL


async def test_approval_settles_once_and_names_the_operator() -> None:
    service, repo, provider_id = await _service_with_pending()

    first = await _decide(service, provider_id)
    replay = await _decide(service, provider_id)

    assert first is not None and replay is None
    call = await _newest(repo, provider_id)
    assert call.status is ToolCallStatus.APPROVED
    assert call.decided_by == _USER


async def test_a_denial_blocks_a_later_approval() -> None:
    service, repo, provider_id = await _service_with_pending()

    denied = await _decide(service, provider_id, approved=False)
    late = await _decide(service, provider_id)

    assert denied is not None and late is None
    assert (await _newest(repo, provider_id)).status is ToolCallStatus.DENIED


async def test_a_decision_on_an_unknown_call_is_a_no_op() -> None:
    service, _, _ = await _service_with_pending()

    assert await _decide(service, "call_unknown") is None


async def test_different_arguments_settle_nothing() -> None:
    """An id says which proposal was answered, not what it would do.

    The run takes the arguments from the request, so accepting a decision that names other arguments
    would let an answered proposal act on a target the operator was never shown while the record
    still reads as their approval.
    """
    service, repo, provider_id = await _service_with_pending()

    settled = await _decide(service, provider_id, arguments={"mission_id": "a-different-mission"})

    assert settled is None
    assert (await _newest(repo, provider_id)).status is ToolCallStatus.AWAITING_APPROVAL


async def test_a_different_verb_settles_nothing() -> None:
    """Six gated verbs take only a mission id, so arguments alone cannot tell them apart.

    Without the name, a decision to unassign a mission would settle a call to delete it, and the
    record would still read as the operator's approval.
    """
    service, repo, provider_id = await _service_with_pending()

    settled = await _decide(service, provider_id, tool_name="leitstand_delete_mission")

    assert settled is None
    assert (await _newest(repo, provider_id)).status is ToolCallStatus.AWAITING_APPROVAL


async def test_a_replay_does_not_reach_an_older_twin() -> None:
    """A replay must not settle a twin left open elsewhere, or the action runs a second time.

    Rows left awaiting are ordinary: starting a new chat, closing a tab, or a storage failure each
    orphan one, and nothing purges them.
    """
    repo = InMemoryToolCallRepository()
    service = _service(repo)
    stranded = await _seed(
        repo,
        provider_id="call_1",
        arguments=_ARGS,
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0,
    )
    current = await _seed(
        repo,
        provider_id="call_1",
        arguments=_ARGS,
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0 + timedelta(minutes=5),
    )

    first = await _decide(service, "call_1")
    replay = await _decide(service, "call_1")

    assert first == current.id
    assert replay is None
    assert (await _row(repo, "call_1", stranded.id)).status is ToolCallStatus.AWAITING_APPROVAL


async def test_an_approval_after_a_denial_spares_the_twin() -> None:
    """Reaching past a denied row to an older twin would actuate what the operator refused."""
    repo = InMemoryToolCallRepository()
    service = _service(repo)
    stranded = await _seed(
        repo,
        provider_id="call_1",
        arguments=_ARGS,
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0,
    )
    await _seed(
        repo,
        provider_id="call_1",
        arguments=_ARGS,
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0 + timedelta(minutes=5),
    )

    denied = await _decide(service, "call_1", approved=False)
    approved = await _decide(service, "call_1")

    assert denied is not None
    assert approved is None
    assert (await _row(repo, "call_1", stranded.id)).status is ToolCallStatus.AWAITING_APPROVAL


async def test_a_decision_settles_by_arguments() -> None:
    """Two proposals can share one id, so the newest is not always the one being answered."""
    repo = InMemoryToolCallRepository()
    service = _service(repo)
    answered = await _seed(
        repo,
        provider_id="call_1",
        arguments=_ARGS,
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0,
    )
    other = await _seed(
        repo,
        provider_id="call_1",
        arguments={"mission_id": "m-2"},
        status=ToolCallStatus.AWAITING_APPROVAL,
        at=_T0 + timedelta(minutes=5),
    )

    settled = await _decide(service, "call_1")

    assert settled == answered.id
    assert (await _row(repo, "call_1", other.id)).status is ToolCallStatus.AWAITING_APPROVAL
