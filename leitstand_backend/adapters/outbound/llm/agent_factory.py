"""The chat agent: model, curated tools, and the approval gate.

Construction performs no I/O, which is what lets the composition root build the agent without
the model endpoint being reachable. The model runs on a separate box and is a soft dependency:
an outage must degrade chat and nothing else.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from functools import cache
from importlib import resources
from types import MappingProxyType
from typing import Any

import httpx
from fastmcp import FastMCP
from openai import AsyncOpenAI
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import UsageLimits

from leitstand_backend.adapters.outbound.llm.domain_mcp import (
    APPROVED_BY_KEY,
    APPROVED_KEY,
    CALL_PROVENANCE_KEY,
    DECISION_ID_KEY,
    DOMAIN_TOOL_PREFIX,
    requires_approval,
)
from leitstand_backend.adapters.outbound.llm.external_toolsets import (
    GuardedMCPToolset,
    SourceLinkToolset,
    is_read_only,
)
from leitstand_backend.infrastructure.external_mcp import ExternalMCPServer
from leitstand_backend.infrastructure.settings import Settings

SYSTEM_PROMPT = (
    resources.files("leitstand_backend.adapters.outbound.llm")
    .joinpath("prompts/assistant.md")
    .read_text(encoding="utf-8")
    .strip()
)


# A union because an approval-required tool pauses the run, returning DeferredToolRequests in place
# of an answer.
ChatAgent = Agent[object, str | DeferredToolRequests]

# How long a turn waits for an external server to connect before it goes on without its tools.
_CONNECT_TIMEOUT_S = 3


@cache
def system_prompt_version() -> str:
    """Identify the current system prompt for the audit record.

    Derived from the prompt's content rather than a hand-maintained label, which would drift and
    leave an audit row naming a prompt that did not produce the action.
    """
    digest = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
    return f"assistant-{digest}"


class _RunScopedMCPToolset(MCPToolset):
    """Open one MCP session per run so a turn cannot inherit another turn's identity.

    A session shared across runs holds the context of whichever turn opened it, so overlapping turns
    audit their writes under the wrong operator. What makes this correct is where the session is
    opened, not that the instance is new: pooling or pre-opening one outside the run brings the
    fault back silently. Configure it through the constructor only, because an attribute set
    afterwards is lost at the next run.
    """

    def __init__(self, client: FastMCP | str, **options: Any) -> None:
        super().__init__(client, **options)
        self._client = client
        self._options = options

    async def for_run(self, ctx: RunContext) -> MCPToolset:
        """Return a fresh instance for this run, built from the same arguments."""
        return _RunScopedMCPToolset(self._client, **self._options)


def _error_body(message: str) -> object:
    """Parse the response body out of a tool error message, in either shape it arrives in.

    The MCP server interpolates the already-parsed body, so it arrives as a Python repr rather than
    JSON, and a body it could not parse itself is appended as text. Parsing may not raise: this only
    improves an error message, and RecursionError is how a deep enough literal surfaces.
    """
    start = message.find("{")
    if start == -1:
        return None
    body = message[start:]
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(body)
        except (ValueError, SyntaxError, RecursionError):
            continue
    return None


def _shape_tool_error(message: str) -> str:
    """Reduce a validation body to the fields that were wrong, so the model can correct the call.

    A whole body reaches the model nested inside an HTTP error, which it reads as a transport fault
    and retries unchanged.
    """
    body = _error_body(message)
    if not isinstance(body, dict):
        return message
    details = body.get("detail")
    if not isinstance(details, list):
        return message
    problems: list[str] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        where = ".".join(str(part) for part in detail.get("loc", ()) if part != "body")
        problem = str(detail.get("msg", "invalid"))
        problems.append(f"{where}: {problem}" if where else problem)
    return "; ".join(problems) if problems else message


async def _forward_call_provenance(
    ctx: RunContext, call_tool: CallToolFunc, name: str, args: dict[str, Any]
) -> ToolResult:
    """Send each call's own approval provenance with it, and shape validation errors on the way back.

    Reads the metadata attached to the decision that resolved this call, so two approvals settled
    together reach the audit writer as two approvers rather than one label shared across both.
    """
    approved = ctx.tool_call_approved
    metadata = ctx.tool_call_metadata if approved else None
    envelope = {
        APPROVED_KEY: approved,
        APPROVED_BY_KEY: metadata.get(APPROVED_BY_KEY) if isinstance(metadata, dict) else None,
        DECISION_ID_KEY: metadata.get(DECISION_ID_KEY) if isinstance(metadata, dict) else None,
    }
    try:
        return await call_tool(name, args, metadata={CALL_PROVENANCE_KEY: envelope})
    except ModelRetry as error:
        raise ModelRetry(_shape_tool_error(str(error))) from error


def _build_model(settings: Settings) -> OpenAIChatModel:
    """Point the agent at the OpenAI-compatible endpoint, failing fast when it is unreachable.

    max_retries=0 because the client's default of 2 silently multiplies both timeouts: an
    unreachable host takes ~10 s instead of ~3 s, and a hung one stays open for three read
    timeouts. A human is watching this turn and can retry.
    """
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            settings.llm_read_timeout_s,
            connect=settings.llm_connect_timeout_s,
        )
    )
    openai_client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else "not-needed",
        http_client=http_client,
        max_retries=0,
    )
    return OpenAIChatModel(settings.llm_model, provider=OpenAIProvider(openai_client=openai_client))


def _requires_approval(ctx: RunContext, tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    """Adapt the gate to pydantic-ai's predicate signature: the decision is by tool name alone."""
    return requires_approval(tool_def.name)


def _external_toolset(name: str, server: ExternalMCPServer) -> AbstractToolset[Any]:
    """Offer one server's allowed read-only tools, with their source, and contain its failures."""
    allowed = frozenset(server.allowed_tools)
    # A session per turn, so a session that broke in one turn is not reused by an overlapping one.
    session = _RunScopedMCPToolset(
        server.url,
        headers=server.headers,
        read_timeout=server.timeout / 1000,
        init_timeout=_CONNECT_TIMEOUT_S,
        tool_error_behavior="failed",
        include_instructions=True,
    )
    offered = session.filtered(lambda ctx, tool: tool.name in allowed and is_read_only(tool))
    guarded = GuardedMCPToolset(SourceLinkToolset(offered), server=name, allowed_tools=allowed)
    return guarded.prefixed(name)


def build_chat_agent(
    settings: Settings,
    domain_mcp: FastMCP,
    external: Mapping[str, ExternalMCPServer] = MappingProxyType({}),
) -> ChatAgent:
    """Build the chat agent against the in-process domain server and any external servers.

    ``output_type`` must include DeferredToolRequests: without it an approval-required tool does not
    pause for the operator, it fails the run with a tool error. External servers are read-only, so
    none of their tools waits for approval.
    """
    model = _build_model(settings)
    # approval_required is applied before prefixed, so the predicate sees the bare operation_id.
    toolset = (
        _RunScopedMCPToolset(domain_mcp, process_tool_call=_forward_call_provenance)
        .approval_required(_requires_approval)
        .prefixed(DOMAIN_TOOL_PREFIX)
    )
    externals = [_external_toolset(name, server) for name, server in external.items()]
    agent = Agent(
        model,
        toolsets=[toolset, *externals],
        instructions=SYSTEM_PROMPT,
        output_type=[str, DeferredToolRequests],
        model_settings=_model_settings(settings),
        retries=1,
    )

    @agent.instructions
    def current_date() -> str:
        """Tell the model the operators' date, which it cannot know itself.

        The date, not the time: instructions come before the conversation, so a line that changed
        every minute would make the model provider's prompt cache miss on almost every turn.
        """
        zone = settings.chat_timezone
        return f"Today is {datetime.now(zone):%A, %d %B %Y} ({zone.key})."

    return agent


def turn_usage_limits(settings: Settings) -> UsageLimits:
    """Cap the work one turn may do.

    A turn that cannot reach an answer does not stop on its own, and the library's own ceiling is
    high enough that the operator waits minutes for an error. A rejected call spends a request but
    no tool budget, so the request allowance doubles it and the tool ceiling stays the one that
    fires.
    """
    return UsageLimits(
        tool_calls_limit=settings.chat_max_tool_calls,
        request_limit=settings.chat_max_tool_calls * 2 + 2,
    )


def _model_settings(settings: Settings) -> OpenAIChatModelSettings:
    """Carry the reasoning switch, which is a chat-template flag rather than an OpenAI parameter."""
    extra_body: dict[str, Any] = {}
    if settings.llm_reasoning == "off":
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}
    return OpenAIChatModelSettings(extra_body=extra_body)
