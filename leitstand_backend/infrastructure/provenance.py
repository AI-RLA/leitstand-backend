"""Provenance of a write: what performed it, and on what authority.

Carried on ContextVars rather than headers because it must be unspoofable: a header could be forged
into "the AI did it", and nothing outside this process can set a ContextVar. They are read ambiently
at the audit writer, so no route or service signature carries them.

Origin says the write arrived through the agent's loopback; approval says an operator authorised
that one call. Separating them is what makes a broken approval channel read as ``autonomous``, the
alarm, rather than as a human acting directly.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from leitstand_backend.domain.model.audit import ACTOR_AI_AGENT, Actor


@dataclass(frozen=True)
class AgentOrigin:
    """What holds for every write the agent makes, fixed for the process."""

    model: str | None = None
    prompt_version: str | None = None
    actor: Actor = ACTOR_AI_AGENT


@dataclass(frozen=True)
class ToolCallProvenance:
    """What holds for one tool call: whether an operator approved it, and which decision did.

    Observed per call rather than inferred from the turn, because one turn can settle two approvals
    at once. A write with no approval behind it stays ``autonomous``, which is the alarm.
    """

    tool_call_id: str | None = None
    approved: bool = False
    approved_by: str | None = None


class AgentOriginBoundary:
    """Mark every request entering through the agent's tool loopback as the agent's.

    Wraps the app the loopback client dials, so the mark is set per delegated call and reset in the
    same await chain. Only that client holds this wrapper: the server dials the app directly, which
    is what keeps an outside caller from ever presenting as the agent.
    """

    def __init__(self, app, origin: AgentOrigin) -> None:
        self.app = app
        self.origin = origin

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = agent_origin.set(self.origin)
        try:
            await self.app(scope, receive, send)
        finally:
            agent_origin.reset(token)


agent_origin: ContextVar[AgentOrigin | None] = ContextVar("agent_origin", default=None)
tool_call_provenance: ContextVar[ToolCallProvenance | None] = ContextVar(
    "tool_call_provenance", default=None
)
