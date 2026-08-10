"""ToolCallRepository that opens a short transaction per call.

Mirrors the per-call session pattern the projector uses. A chat turn waits on a remote model for
seconds, so a request-scoped session would pin a pooled connection for that whole time and a
handful of concurrent chats would starve the REST routes that control robots.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leitstand_backend.adapters.outbound.persistence.postgres.tool_call_repository_adapter import (
    PostgresToolCallRepositoryAdapter,
)
from leitstand_backend.domain.model.chat.tool_call import ToolCall
from leitstand_backend.infrastructure.db import transactional_scope
from leitstand_backend.ports.outbound.tool_call_repository import ToolCallRepository


class SessionScopedToolCallRepository(ToolCallRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add_tool_call(self, tool_call: ToolCall) -> ToolCall:
        async with transactional_scope(self._session_factory) as session:
            return await PostgresToolCallRepositoryAdapter(session).add_tool_call(tool_call)

    async def list_tool_calls_by_provider_id(
        self, user_id: str, provider_tool_call_id: str
    ) -> list[ToolCall]:
        async with transactional_scope(self._session_factory) as session:
            return await PostgresToolCallRepositoryAdapter(session).list_tool_calls_by_provider_id(
                user_id, provider_tool_call_id
            )

    async def settle_tool_call(
        self,
        tool_call_id: UUID,
        *,
        decided_by: str,
        approved: bool,
    ) -> bool:
        async with transactional_scope(self._session_factory) as session:
            return await PostgresToolCallRepositoryAdapter(session).settle_tool_call(
                tool_call_id, decided_by=decided_by, approved=approved
            )

    async def record_tool_call_outcome(
        self,
        tool_call_id: UUID,
        *,
        succeeded: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        async with transactional_scope(self._session_factory) as session:
            return await PostgresToolCallRepositoryAdapter(session).record_tool_call_outcome(
                tool_call_id,
                succeeded=succeeded,
                result=result,
                error=error,
            )
