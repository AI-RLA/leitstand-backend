"""In-memory AuditLog fake (test-only)."""

from __future__ import annotations

from leitstand_backend.ports.outbound.audit_log import AuditLog


class InMemoryAuditLog(AuditLog):
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def append(
        self,
        action: str,
        user_id: str | None,
        target_type: str | None,
        target_id: str | None,
        payload: dict | None,
    ) -> None:
        self.entries.append(
            {
                "action": action,
                "user_id": user_id,
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            }
        )
