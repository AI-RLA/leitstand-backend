"""Postgres-backed ToolCallRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.audit_log_adapter import (
    PostgresAuditLogAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.models import ToolCallRow
from leitstand_backend.domain.model.audit import (
    ACTOR_AI_AGENT,
    AUTHORITY_APPROVED_PROPOSAL,
)
from leitstand_backend.domain.model.chat.tool_call import ToolCall, ToolCallStatus
from leitstand_backend.ports.outbound.tool_call_repository import ToolCallRepository


class PostgresToolCallRepositoryAdapter(ToolCallRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_tool_call(self, tool_call: ToolCall) -> ToolCall:
        stmt = (
            insert(ToolCallRow)
            .values(
                id=tool_call.id,
                user_id=tool_call.user_id,
                provider_tool_call_id=tool_call.provider_tool_call_id,
                tool_name=tool_call.tool_name,
                arguments=tool_call.arguments,
                result=tool_call.result,
                status=tool_call.status.value,
                decided_by=tool_call.decided_by,
                decided_at=tool_call.decided_at,
                error=tool_call.error,
                model=tool_call.model,
                prompt_version=tool_call.prompt_version,
                created_at=tool_call.created_at,
            )
            .returning(ToolCallRow)
        )
        row = (await self._session.execute(stmt)).scalar_one()
        return _tool_call_to_domain(row)

    async def list_tool_calls_by_provider_id(
        self, user_id: str, provider_tool_call_id: str
    ) -> list[ToolCall]:
        stmt = (
            select(ToolCallRow)
            .where(
                ToolCallRow.user_id == user_id,
                ToolCallRow.provider_tool_call_id == provider_tool_call_id,
            )
            .order_by(ToolCallRow.created_at.desc(), ToolCallRow.id.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_tool_call_to_domain(row) for row in rows]

    async def settle_tool_call(
        self,
        tool_call_id: UUID,
        *,
        decided_by: str,
        approved: bool,
    ) -> bool:
        """Compare-and-set out of ``awaiting_approval`` and record the decision in one transaction.

        The predicate is re-evaluated after the row lock releases, so of two racing decisions only
        one moves the row and the other is told it changed nothing. The audit row commits with the
        flip, so the operator's decision and the record of it are never split by a crash between
        two transactions.
        """
        new_status = ToolCallStatus.APPROVED if approved else ToolCallStatus.DENIED
        stmt = (
            update(ToolCallRow)
            .where(
                ToolCallRow.id == tool_call_id,
                ToolCallRow.status == ToolCallStatus.AWAITING_APPROVAL.value,
            )
            .values(status=new_status.value, decided_by=decided_by, decided_at=_now())
            .returning(
                ToolCallRow.tool_name,
                ToolCallRow.arguments,
                ToolCallRow.user_id,
                ToolCallRow.created_at,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return False
        # The operator deciding on a proposal is acting on their own direct authority, so this keeps
        # the writer's human/direct defaults.
        await PostgresAuditLogAdapter(self._session).append(
            action="tool_call.approved" if approved else "tool_call.denied",
            user_id=row.user_id,
            target_type="tool_call",
            target_id=str(tool_call_id),
            payload={
                "tool_name": row.tool_name,
                "arguments": row.arguments,
                # A datetime would fail the JSONB insert while recording a decision already made.
                "proposed_at": row.created_at.isoformat(),
            },
            decided_by=decided_by,
            tool_call_id=str(tool_call_id),
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
        """Move one approved call to its outcome, naming the row by its primary key.

        The natural key is not unique, so an UPDATE over it would close every approved match
        together. On failure the audit row commits with the flip, since the fleet-mutation row it
        would have written rolled back; its provenance is copied from the settled call the flip
        returns, never from the ambient turn context, which is stale by the time an outcome lands.
        """
        status = ToolCallStatus.SUCCEEDED if succeeded else ToolCallStatus.FAILED
        stmt = (
            update(ToolCallRow)
            .where(
                ToolCallRow.id == tool_call_id,
                ToolCallRow.status == ToolCallStatus.APPROVED.value,
            )
            .values(status=status.value, result=result, error=error)
            .returning(
                ToolCallRow.id,
                ToolCallRow.tool_name,
                ToolCallRow.arguments,
                ToolCallRow.decided_by,
                ToolCallRow.user_id,
                ToolCallRow.model,
                ToolCallRow.prompt_version,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return False
        if not succeeded:
            await PostgresAuditLogAdapter(self._session).append(
                action="tool_call.failed",
                user_id=row.user_id,
                target_type="tool_call",
                target_id=str(row.id),
                payload={"tool_name": row.tool_name, "arguments": row.arguments, "error": error},
                actor=ACTOR_AI_AGENT,
                authority=AUTHORITY_APPROVED_PROPOSAL,
                decided_by=row.decided_by,
                model=row.model,
                prompt_version=row.prompt_version,
                tool_call_id=str(row.id),
            )
        return True


def _tool_call_to_domain(row: ToolCallRow) -> ToolCall:
    return ToolCall(
        id=row.id,
        user_id=row.user_id,
        provider_tool_call_id=row.provider_tool_call_id,
        tool_name=row.tool_name,
        arguments=row.arguments,
        result=row.result,
        status=row.status,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        error=row.error,
        model=row.model,
        prompt_version=row.prompt_version,
        created_at=row.created_at,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
