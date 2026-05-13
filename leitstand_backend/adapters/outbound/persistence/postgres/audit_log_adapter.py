"""Postgres-backed AuditLog."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.adapters.outbound.persistence.postgres.models import AuditLogRow
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
    ) -> None:
        row = AuditLogRow(
            ts=datetime.now(timezone.utc),
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
        self._session.add(row)
