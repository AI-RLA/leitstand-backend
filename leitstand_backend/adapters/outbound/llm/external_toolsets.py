"""Wrappers that confine an external MCP server's failures to its tools and pass on its sources."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import structlog
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.ui.vercel_ai.response_types import SourceUrlChunk

logger = structlog.get_logger(__name__)


@dataclass
class _MCPServerMemory:
    """What the guard keeps about its server from one turn to the next."""

    last_tools: dict[str, ToolsetTool[Any]] = field(default_factory=dict)
    logged_missing: set[str] = field(default_factory=set)


@dataclass
class GuardedMCPToolset(WrapperToolset[Any]):
    """Confine an external server's failures to its own tools, for one turn.

    Any error from the server, including a protocol error pydantic-ai raises as a retry, marks it
    unavailable for the rest of the turn, because telling a timeout from a malformed answer is not
    worth a second wait.
    """

    server: str
    allowed_tools: frozenset[str]
    # Created once, then handed on by for_run, so every turn's copy of the guard shares it.
    memory: _MCPServerMemory = field(default_factory=_MCPServerMemory)
    _available: bool = field(default=False, init=False)
    _entered: bool = field(default=False, init=False)

    async def for_run(self, ctx: RunContext[Any]) -> GuardedMCPToolset:
        """Start every turn with a fresh guard, whatever the wrapped toolset does per run."""
        return replace(self, wrapped=await self.wrapped.for_run(ctx))

    async def __aenter__(self) -> GuardedMCPToolset:
        try:
            await self.wrapped.__aenter__()
        except Exception as error:  # noqa: BLE001 - only this server's tools are lost
            logger.warning("external_mcp_unavailable", server=self.server, reason=str(error))
            return self
        self._entered = self._available = True
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        if not self._entered:
            return None
        try:
            return await self.wrapped.__aexit__(*args)
        except Exception as error:  # noqa: BLE001 - the answer is already complete
            logger.warning("external_mcp_close_failed", server=self.server, reason=str(error))
            return None

    async def get_instructions(
        self, ctx: RunContext[Any]
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        if not self._available:
            return (
                f"{self._unavailable()} Say so if the operator asks for it, "
                "and do not call its tools."
            )
        return await self.wrapped.get_instructions(ctx)

    async def get_tools(self, ctx: RunContext[Any]) -> dict[str, ToolsetTool[Any]]:
        # Offering the tools the model saw last keeps a call to one from ending the turn as an
        # unknown tool.
        if not self._available:
            return self.memory.last_tools
        try:
            tools = await self.wrapped.get_tools(ctx)
        except Exception as error:  # noqa: BLE001 - only this server's tools are lost
            self._available = False
            logger.warning("external_mcp_unavailable", server=self.server, reason=str(error))
            return self.memory.last_tools
        self._report_missing(tools)
        self.memory.last_tools = dict(tools)
        return tools

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        if not self._available:
            raise ToolFailed(self._unavailable())
        started, ok = time.monotonic(), False
        try:
            result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
            ok = True
            return result
        except ToolFailed:
            raise
        except Exception as error:  # noqa: BLE001 - includes ModelRetry, raised for MCP timeouts
            self._available = False
            raise ToolFailed(self._unavailable()) from error
        finally:
            duration_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "external_mcp_call", server=self.server, tool=name, duration_ms=duration_ms, ok=ok
            )

    def _unavailable(self) -> str:
        return f"The {self.server} service is unavailable this turn."

    def _report_missing(self, tools: dict[str, ToolsetTool[Any]]) -> None:
        for name in sorted(self.allowed_tools - tools.keys() - self.memory.logged_missing):
            self.memory.logged_missing.add(name)
            logger.warning("external_mcp_tool_missing", server=self.server, tool=name)


@dataclass
class SourceLinkToolset(WrapperToolset[Any]):
    """Attach the source a server declares for a tool to each result, for the chat stream.

    The metadata of a tool result never reaches the model, so the link costs no tokens.
    """

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any], tool: ToolsetTool[Any]
    ) -> Any:
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        source = _declared_source(tool)
        if source is None:
            return result
        title, url = source
        return ToolReturn(
            return_value=result,
            metadata=SourceUrlChunk(source_id=ctx.tool_call_id, title=title, url=url),
        )


def _declared_source(tool: ToolsetTool[Any]) -> tuple[str, str] | None:
    """Read the title and URL a server declared for a tool in its MCP metadata, if any."""
    source = _nested_dict(_nested_dict(tool.tool_def.metadata, "meta"), "source")
    if all(isinstance(source.get(key), str) for key in ("title", "url")):
        return source["title"], source["url"]
    return None


def is_read_only(tool: ToolDefinition) -> bool:
    """Tell whether a server marks a tool as changing nothing, the only kind that skips approval."""
    return _nested_dict(tool.metadata, "annotations").get("readOnlyHint") is True


def _nested_dict(value: Any, key: str) -> dict[str, Any]:
    """Return the object under a key of server-supplied metadata, or an empty one."""
    inner = value.get(key) if isinstance(value, dict) else None
    return inner if isinstance(inner, dict) else {}
