"""ToolCall: an agent's proposed tool invocation and its approval / execution lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class ToolCallStatus(str, Enum):
    """Lifecycle of a single tool call, from the human-in-the-loop gate to its outcome.

    Only calls that need a decision are recorded, so one is born awaiting a decision rather than
    pending: a read the agent runs on its own never reaches this table.
    """

    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    DENIED = "denied"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ToolCall(BaseModel):
    """An agent's proposed tool call, tracked from the gate to its outcome.

    Updated in place, so one row answers what was proposed, who decided, and whether it ran.
    ``provider_tool_call_id`` is the model-minted id the approval round-trip correlates on, distinct
    from ``id``, the storage key.
    """

    id: UUID
    user_id: str
    provider_tool_call_id: str
    tool_name: str
    arguments: dict | None = None
    result: dict | None = None
    status: ToolCallStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    error: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime
