"""Audit log Port (write-only for now)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable


class AuditLog(ABC):
    @abstractmethod
    async def append(
        self,
        action: str,
        user_id: str | None,
        target_type: str | None,
        target_id: str | None,
        payload: dict | None,
    ) -> None: ...


AuditWriter = Callable[
    [str, str | None, str | None, dict | None],
    Awaitable[None],
]
