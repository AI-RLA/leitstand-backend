"""Behaviour every ToolCallRepository implementation must exhibit.

The fake defines the contract. The Postgres adapter has no test against it here: that needs a
dedicated database, kept out of the hermetic suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from leitstand_backend.domain.model.chat.tool_call import ToolCall, ToolCallStatus
from tests.fakes.in_memory_tool_call_repository import InMemoryToolCallRepository

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


def _tool_call(user_id: str, *, provider_id: str, status: ToolCallStatus) -> ToolCall:
    return ToolCall(
        id=uuid4(),
        user_id=user_id,
        provider_tool_call_id=provider_id,
        tool_name="dispatch_mission",
        status=status,
        created_at=_T0,
    )


async def _newest(repo, user_id: str, provider_id: str) -> ToolCall | None:
    """The head of the list, which is the row the settling rule considers."""
    rows = await repo.list_tool_calls_by_provider_id(user_id, provider_id)
    return rows[0] if rows else None


class ToolCallRepositoryContract:
    async def test_the_lookup_is_scoped_to_the_owner(self, repo) -> None:
        """The owner is the only scope the lookup has, so it carries that isolation by itself."""
        tool_call = _tool_call(
            "op-1", provider_id="call_scoped", status=ToolCallStatus.AWAITING_APPROVAL
        )
        await repo.add_tool_call(tool_call)

        assert [
            call.id for call in await repo.list_tool_calls_by_provider_id("op-1", "call_scoped")
        ] == [tool_call.id]
        assert await repo.list_tool_calls_by_provider_id("op-2", "call_scoped") == []

    async def test_rows_come_back_newest_first(self, repo) -> None:
        """The model's id is not unique beyond one run, so an operator may hold several.

        The caller reads the head, so order is what stops an older twin being reached.
        """
        older = _tool_call("op-1", provider_id="call_1", status=ToolCallStatus.SUCCEEDED)
        newest = _tool_call(
            "op-1", provider_id="call_1", status=ToolCallStatus.AWAITING_APPROVAL
        ).model_copy(update={"created_at": _T0 + timedelta(minutes=5)})
        await repo.add_tool_call(older)
        await repo.add_tool_call(newest)

        rows = await repo.list_tool_calls_by_provider_id("op-1", "call_1")

        assert [call.id for call in rows] == [newest.id, older.id]

    async def test_settle_tool_call_is_exactly_once(self, repo) -> None:
        tool_call = _tool_call(
            "op-1", provider_id="call_ok", status=ToolCallStatus.AWAITING_APPROVAL
        )
        await repo.add_tool_call(tool_call)

        first = await repo.settle_tool_call(tool_call.id, decided_by="op-1", approved=True)
        replay = await repo.settle_tool_call(tool_call.id, decided_by="op-1", approved=True)

        assert (first, replay) == (True, False)
        settled = await _newest(repo, "op-1", "call_ok")
        assert settled is not None
        assert settled.status == ToolCallStatus.APPROVED
        assert settled.decided_by == "op-1"
        assert settled.decided_at is not None

    async def test_deny_tool_call_blocks_later_approval(self, repo) -> None:
        tool_call = _tool_call(
            "op-1", provider_id="call_no", status=ToolCallStatus.AWAITING_APPROVAL
        )
        await repo.add_tool_call(tool_call)

        denied = await repo.settle_tool_call(tool_call.id, decided_by="op-1", approved=False)
        late = await repo.settle_tool_call(tool_call.id, decided_by="op-1", approved=True)

        assert (denied, late) == (True, False)
        settled = await _newest(repo, "op-1", "call_no")
        assert settled is not None
        assert settled.status == ToolCallStatus.DENIED

    async def test_settling_an_unknown_tool_call_is_false(self, repo) -> None:
        assert await repo.settle_tool_call(uuid4(), decided_by="op-1", approved=True) is False

    async def test_an_outcome_closes_only_the_row_it_names(self, repo) -> None:
        """Two approved rows can share the model's id, and only the one that ran may be closed.

        Closing by the shared id instead would stamp both, so a call would come to read as having
        succeeded on the strength of an execution that was not its own.
        """
        ran = _tool_call("op-1", provider_id="call_1", status=ToolCallStatus.AWAITING_APPROVAL)
        stranded = _tool_call(
            "op-1", provider_id="call_1", status=ToolCallStatus.AWAITING_APPROVAL
        ).model_copy(update={"created_at": _T0 + timedelta(minutes=5)})
        await repo.add_tool_call(ran)
        await repo.add_tool_call(stranded)
        await repo.settle_tool_call(ran.id, decided_by="op-1", approved=True)
        await repo.settle_tool_call(stranded.id, decided_by="op-1", approved=True)

        assert await repo.record_tool_call_outcome(ran.id, succeeded=True) is True

        rows = {
            call.id: call for call in await repo.list_tool_calls_by_provider_id("op-1", "call_1")
        }
        assert rows[ran.id].status is ToolCallStatus.SUCCEEDED
        assert rows[stranded.id].status is ToolCallStatus.APPROVED

    async def test_outcome_only_closes_an_approved_call(self, repo) -> None:
        tool_call = _tool_call(
            "op-1", provider_id="call_out", status=ToolCallStatus.AWAITING_APPROVAL
        )
        await repo.add_tool_call(tool_call)

        # Awaiting approval: nothing to close yet.
        assert await repo.record_tool_call_outcome(tool_call.id, succeeded=True) is False

        await repo.settle_tool_call(tool_call.id, decided_by="op-1", approved=True)
        recorded = await repo.record_tool_call_outcome(
            tool_call.id, succeeded=True, result={"content": "ok"}
        )

        assert recorded is True
        settled = await _newest(repo, "op-1", "call_out")
        assert settled is not None
        assert settled.status == ToolCallStatus.SUCCEEDED
        assert settled.result == {"content": "ok"}


class TestFakeToolCallRepository(ToolCallRepositoryContract):
    @pytest.fixture
    def repo(self) -> InMemoryToolCallRepository:
        return InMemoryToolCallRepository()
