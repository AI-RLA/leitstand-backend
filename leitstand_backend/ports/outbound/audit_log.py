"""Audit log Port (write-only for now)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from leitstand_backend.domain.model.audit import (
    ACTOR_HUMAN,
    AUTHORITY_DIRECT,
    Actor,
    Authority,
)


class AuditLog(ABC):
    @abstractmethod
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
        """Record an action with its provenance.

        The provenance keyword-args default to a direct human action, which is the case outside an
        agent turn. ``tool_call_id`` is an opaque key correlating a settled tool call with the
        action it authorised, kept meaningful after chat rows purge.

        Every field here is server-set from the authenticated caller, never from the request body.
        """


AuditWriter = Callable[
    [str, str | None, str | None, dict | None],
    Awaitable[None],
]
