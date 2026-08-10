"""Postgres-backed AuditLog."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import AuditLogRow
from leitstand_backend.domain.model.audit import (
    ACTOR_HUMAN,
    AUTHORITY_DIRECT,
    Actor,
    Authority,
)
from leitstand_backend.ports.outbound.audit_log import AuditLog


class PostgresAuditLogAdapter(AuditLog):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        action: str,
        user_id: str | None,
        target_type: str | None,
        target_id: str | None,
        payload: dict | None,
        *,
        actor: Actor = ACTOR_HUMAN,
        authority: Authority = AUTHORITY_DIRECT,
        decided_by: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        row = AuditLogRow(
            ts=datetime.now(timezone.utc),
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            actor=actor,
            authority=authority,
            decided_by=decided_by,
            model=model,
            prompt_version=prompt_version,
            tool_call_id=UUID(tool_call_id) if tool_call_id else None,
        )
        self._session.add(row)
