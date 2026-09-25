"""The external MCP servers the AI assistant may use, read from one JSON file at startup."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import structlog
from fastmcp.mcp_config import infer_transport_type_from_url
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = structlog.get_logger(__name__)

# OpenAI and Gemini accept at most 64 characters per tool name, the server's prefix included.
_MAX_PREFIXED_NAME = 64
_MAX_SERVER_NAME = 24
_MAX_TOOL_NAME = _MAX_PREFIXED_NAME - _MAX_SERVER_NAME - len("_")

# No underscore in a server name, so a prefixed tool name cannot collide with another server's.
_SERVER_NAME = re.compile(r"[a-z][a-z0-9]*")
_TOOL_NAME = r"^[a-zA-Z0-9_-]+$"

# ${VAR}, or ${VAR:-default} for an unset or empty VAR, as in other mcpServers files.
_ENV_VARIABLE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


class ExternalMCPServer(BaseModel):
    """One configured server: where it is and which of its tools the AI assistant may use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: int = Field(default=10_000, gt=0, le=60_000, description="Milliseconds per call.")
    allowed_tools: tuple[
        Annotated[str, Field(pattern=_TOOL_NAME, max_length=_MAX_TOOL_NAME)], ...
    ] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def _streamable_http_url(cls, url: str) -> str:
        # A bad host or port is left to fail at connect, where the guard contains it.
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("must be an http or https URL")
        if infer_transport_type_from_url(url) != "http":
            raise ValueError("must be a Streamable HTTP endpoint, not SSE")
        return url


def load_external_servers(path: Path | None, reserved_prefix: str) -> dict[str, ExternalMCPServer]:
    """Read the server list, skipping an invalid entry or one that takes the reserved prefix."""
    if path is None:
        return {}
    try:
        servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
        if not isinstance(servers, dict):
            raise TypeError("mcpServers is not an object")
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning("external_mcp_config_unreadable", path=str(path), reason=str(error))
        return {}

    loaded: dict[str, ExternalMCPServer] = {}
    for name, entry in servers.items():
        valid = _SERVER_NAME.fullmatch(name) and len(name) <= _MAX_SERVER_NAME
        if not valid or name.startswith(reserved_prefix):
            logger.warning(
                "external_mcp_entry_invalid",
                server=name,
                reason=f"name must be up to {_MAX_SERVER_NAME} lower-case letters and digits, "
                f"not starting with {reserved_prefix}",
            )
            continue
        try:
            loaded[name] = ExternalMCPServer.model_validate(_expand_env_vars(entry))
        except ValueError as error:  # a pydantic ValidationError is a ValueError too
            logger.warning("external_mcp_entry_invalid", server=name, reason=str(error))
    return loaded


def _expand_env_vars(value: Any) -> Any:
    """Fill in the environment variables referenced in every string of an entry."""
    if isinstance(value, str):
        return _ENV_VARIABLE.sub(_env_value, value)
    if isinstance(value, dict):
        return {key: _expand_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _env_value(match: re.Match[str]) -> str:
    name, default = match["name"], match["default"]
    value = os.environ.get(name)
    if default is not None and not value:
        return default
    if value is None:
        raise ValueError(f"environment variable {name} is not set and has no default")
    return value
