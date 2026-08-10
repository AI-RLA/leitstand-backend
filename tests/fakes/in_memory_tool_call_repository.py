"""In-memory ToolCallRepository fake (test-only)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import UUID

from leitstand_backend.domain.model.chat.tool_call import ToolCall, ToolCallStatus
from leitstand_backend.ports.outbound.tool_call_repository import ToolCallRepository


class InMemoryToolCallRepository(ToolCallRepository):
    def __init__(self) -> None:
        self._tool_calls: dict[UUID, ToolCall] = {}
        self._lock = threading.Lock()

    async def add_tool_call(self, tool_call: ToolCall) -> ToolCall:
        with self._lock:
            self._tool_calls[tool_call.id] = tool_call
        return tool_call

    async def list_tool_calls_by_provider_id(
        self, user_id: str, provider_tool_call_id: str
    ) -> list[ToolCall]:
        with self._lock:
            matches = [
                tool_call
                for tool_call in self._tool_calls.values()
                if tool_call.user_id == user_id
                and tool_call.provider_tool_call_id == provider_tool_call_id
            ]
        matches.sort(key=lambda call: (call.created_at, call.id), reverse=True)
        return matches

    async def settle_tool_call(
        self,
        tool_call_id: UUID,
        *,
        decided_by: str,
        approved: bool,
    ) -> bool:
        with self._lock:
            tool_call = self._tool_calls.get(tool_call_id)
            if tool_call is None or tool_call.status != ToolCallStatus.AWAITING_APPROVAL:
                return False
            new_status = ToolCallStatus.APPROVED if approved else ToolCallStatus.DENIED
            self._tool_calls[tool_call_id] = tool_call.model_copy(
                update={
                    "status": new_status,
                    "decided_by": decided_by,
                    "decided_at": _now(),
                }
            )
        return True

    async def record_tool_call_outcome(
        self,
        tool_call_id: UUID,
        *,
        succeeded: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        with self._lock:
            call = self._tool_calls.get(tool_call_id)
            if call is None or call.status is not ToolCallStatus.APPROVED:
                return False
            self._tool_calls[tool_call_id] = call.model_copy(
                update={
                    "status": ToolCallStatus.SUCCEEDED if succeeded else ToolCallStatus.FAILED,
                    "result": result,
                    "error": error,
                }
            )
        return True


def _now() -> datetime:
    return datetime.now(timezone.utc)
