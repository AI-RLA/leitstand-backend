"""The chat agent: model, curated tools, and the approval gate.

Construction performs no I/O, which is what lets the composition root build the agent without
the model endpoint being reachable. The model runs on a separate box and is a soft dependency:
an outage must degrade chat and nothing else.
"""

from __future__ import annotations

import ast
import hashlib
import json
from functools import cache
from importlib import resources
from typing import Any

import httpx
from fastmcp import FastMCP
from openai import AsyncOpenAI
from pydantic_ai import Agent, DeferredToolRequests, ModelRetry, RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ProcessToolCallback, ToolResult
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from leitstand_backend.adapters.outbound.llm.domain_mcp import (
    APPROVED_BY_KEY,
    APPROVED_KEY,
    CALL_PROVENANCE_KEY,
    DECISION_ID_KEY,
    requires_approval,
)
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
    fault back silently.
    """

    def __init__(
        self, server: FastMCP, *, process_tool_call: ProcessToolCallback | None = None
    ) -> None:
        super().__init__(server, process_tool_call=process_tool_call)
        self._domain_mcp = server

    async def for_run(self, ctx: RunContext) -> MCPToolset:
        """Return a fresh instance for this run, carrying the configuration forward.

        The base class resets every field to its default, so anything not copied here is dropped
        for the rest of the run, silently.
        """
        fresh = _RunScopedMCPToolset(self._domain_mcp, process_tool_call=self.process_tool_call)
        fresh.tool_error_behavior = self.tool_error_behavior
        fresh.max_retries = self.max_retries
        fresh.cache_tools = self.cache_tools
        fresh.cache_resources = self.cache_resources
        fresh.cache_prompts = self.cache_prompts
        return fresh


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


def build_chat_agent(settings: Settings, domain_mcp: FastMCP) -> ChatAgent:
    """Build the chat agent against an in-process MCP server.

    ``output_type`` must include DeferredToolRequests: without it an approval-required tool does not
    pause for the operator, it fails the run with a tool error.
    """
    model = _build_model(settings)
    # approval_required is applied before prefixed, so the predicate sees the bare operation_id.
    toolset = (
        _RunScopedMCPToolset(domain_mcp, process_tool_call=_forward_call_provenance)
        .approval_required(_requires_approval)
        .prefixed("leitstand")
    )
    return Agent(
        model,
        toolsets=[toolset],
        instructions=SYSTEM_PROMPT,
        output_type=[str, DeferredToolRequests],
        model_settings=_model_settings(settings),
        retries=1,
    )


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
