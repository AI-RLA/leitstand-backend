"""Tool-call approval lifecycle: record a proposal, settle its decision, record its outcome.

No conversation content is stored: only the proposed fleet-changing calls, their decisions, and
their outcomes.

Bound to one operator for its lifetime, so there is no way to ask it about another operator's tool
calls. Every operator is the same dummy user today, which is why that scoping is structural.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from leitstand_backend.domain.model.chat.tool_call import ToolCall, ToolCallStatus
from leitstand_backend.domain.user import User
from leitstand_backend.ports.outbound.tool_call_repository import ToolCallRepository


class ToolCallApprovalService:
    def __init__(self, tool_calls: ToolCallRepository, user: User) -> None:
        self._tool_calls = tool_calls
        self._user_id = str(user.id)

    async def record_pending_tool_call(
        self,
        *,
        provider_tool_call_id: str,
        tool_name: str,
        arguments: dict | None,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> ToolCall:
        """Record a tool call the run paused on, awaiting the operator's approval.

        Keyed by the provider-minted id the approval round-trip correlates on; the decision that
        follows lands on the same row. The turn's model and prompt version are stamped now because
        an outcome landing later reads them from the row, by which time the turn is over.
        """
        return await self._tool_calls.add_tool_call(
            ToolCall(
                id=uuid4(),
                user_id=self._user_id,
                provider_tool_call_id=provider_tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                status=ToolCallStatus.AWAITING_APPROVAL,
                model=model,
                prompt_version=prompt_version,
                created_at=_now(),
            )
        )

    async def record_decision(
        self,
        *,
        provider_tool_call_id: str,
        decided_by: str,
        approved: bool,
        tool_name: str | None,
        arguments: dict | None,
    ) -> UUID | None:
        """Record the operator's approve or deny of a pending tool call, exactly once.

        Returns the settled row's id, which the audit row for whatever it authorises also carries.

        The verb must match as well as the arguments: several gated verbs take only a mission id, so
        an approval to unassign a mission would otherwise settle a delete. Only the newest proposal
        is eligible, and only while it is still awaiting a decision; reaching past it to an older
        twin would let a replay, or an approve after a deny, actuate a stranded proposal.
        """
        candidates = await self._tool_calls.list_tool_calls_by_provider_id(
            self._user_id, provider_tool_call_id
        )
        match = next(
            (
                call
                for call in candidates
                if call.tool_name == tool_name and call.arguments == arguments
            ),
            None,
        )
        if match is None or match.status is not ToolCallStatus.AWAITING_APPROVAL:
            return None
        settled = await self._tool_calls.settle_tool_call(
            match.id, decided_by=decided_by, approved=approved
        )
        return match.id if settled else None

    async def record_tool_outcome(
        self,
        tool_call_id: UUID,
        *,
        succeeded: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        """Close an approved call with what came of it, so the decision and its result are one row.

        Takes the id the decision returned rather than the model's own, so the outcome lands on the
        call that was settled and on no other.
        """
        return await self._tool_calls.record_tool_call_outcome(
            tool_call_id,
            succeeded=succeeded,
            result=result,
            error=error,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)
