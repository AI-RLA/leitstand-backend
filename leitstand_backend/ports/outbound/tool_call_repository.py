"""Tool-call persistence port: an agent's proposed actions and their decisions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from leitstand_backend.domain.model.chat.tool_call import ToolCall


class ToolCallRepository(ABC):
    @abstractmethod
    async def add_tool_call(self, tool_call: ToolCall) -> ToolCall:
        """Insert a proposed tool call under the caller-minted storage ``id``.

        Insert-only: a decision and an outcome each move the row through their own compare-and-set,
        so a second write under the same id could only undo one of them.
        """

    @abstractmethod
    async def list_tool_calls_by_provider_id(
        self, user_id: str, provider_tool_call_id: str
    ) -> list[ToolCall]:
        """Return this operator's tool calls carrying that provider-minted id, newest first.

        Nothing makes that id unique beyond one run, so several rows can carry it and the order is
        what lets the caller pick. Every status comes back: which may still be settled is the
        application layer's call, kept there rather than split between a query and a
        compare-and-set.
        """

    @abstractmethod
    async def settle_tool_call(
        self,
        tool_call_id: UUID,
        *,
        decided_by: str,
        approved: bool,
    ) -> bool:
        """Atomically move an awaiting-approval tool call to approved or denied, and record it.

        Return True only for the call that actually moved the row out of ``awaiting_approval``; a
        replayed decision returns False, so a caller can enforce exactly-once actuation. The
        operator-attributed record of the decision commits in the same transaction as the flip, so
        the two can never be split, and it is written self-contained to outlive the tool call.
        """

    @abstractmethod
    async def record_tool_call_outcome(
        self,
        tool_call_id: UUID,
        *,
        succeeded: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        """Close out one approved call with what came of it, naming the row by its own id.

        Only an approved call can have an outcome, so a denied or already-closed one is left alone
        and False comes back. A failure commits a durable record of it in the same transaction as
        the flip.

        The row is named by its own id rather than the model's, which is not unique enough to
        close by.
        """
