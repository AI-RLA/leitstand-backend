"""Audit log entry domain model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    user_id: str | None
    action: str
    target_id: str | None
    payload: dict | None
