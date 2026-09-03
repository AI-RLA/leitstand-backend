"""Typed env-based settings."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# RFC 7230 token characters, and the whole string must be one: the browser cannot set headers on a
# WebSocket handshake and sends the token as a subprotocol, which a trailing newline off the end of
# a secret file would break.
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9!#$%&'*+\-.^_`|~]+")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEITSTAND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    http_host: str = Field(default="127.0.0.1")
    http_port: int = Field(default=8080)
    log_level: str = Field(default="INFO")
    zenoh_endpoint: str = Field(default="tcp/127.0.0.1:7447")
    zenoh_config: str | None = Field(default=None)
    zenoh_disabled: bool = Field(default=False)

    database_url: str = Field(
        default="postgresql+asyncpg://leitstand:leitstand@localhost:5432/leitstand"
    )
    auto_migrate: bool = Field(default=True)
    db_pool_size: int = Field(default=10)

    # LLM inference (any OpenAI-compatible host; base_url + model select the provider). A soft
    # dependency: it runs on a separate box and must never gate boot, liveness or readiness.
    llm_base_url: str = Field(default="http://localhost:8000/v1")
    llm_api_key: str | None = Field(default=None)
    llm_model: str = Field(default="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
    # Split deliberately: generation is genuinely slow, but an unreachable host must fail in
    # seconds rather than hold the turn open for the whole read timeout.
    llm_connect_timeout_s: int = Field(default=3)
    llm_read_timeout_s: int = Field(default=60)
    llm_reasoning: Literal["off", "on"] = Field(default="off")

    coverage_planner_url: str | None = Field(default=None)
    coverage_planner_connect_timeout_s: int = Field(default=3)
    coverage_planner_read_timeout_s: int = Field(default=60)
    coverage_turn_sample_m: float = Field(default=0.25, gt=0)
    # How fast curvature may change per metre, in 1/m2. Fields2Cover defaults to 2.0 for a
    # slow-steering machine; 200 lets it change freely, which suits a controller that smooths.
    coverage_linear_curv_change: float = Field(default=200.0, gt=0)

    # Chat surface. Gates exposure only, never safety: an unreachable model stays harmless whatever
    # this says.
    chat_enabled: bool = Field(default=True)
    # Bounds a turn that will not converge: asked something it cannot work out, the model calls
    # tools in a cycle until something stops it.
    chat_max_tool_calls: int = Field(default=10)

    # Auth. Null token = dev-open; a set token is required on REST and the WS endpoint.
    auth_bearer_token: str | None = Field(default=None)
    # NoDecode because a list-typed variable is otherwise JSON-decoded by the settings source
    # itself, which fails before any validator runs and takes the process down with it.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("auth_bearer_token")
    @classmethod
    def _token_can_ride_a_websocket_subprotocol(cls, token: str | None) -> str | None:
        """Refuse a token the browser cannot send, rather than losing the live fleet stream to it.

        The WebSocket handshake carries the token as a subprotocol, which may only hold RFC 7230
        token characters. A base64 secret contains none of `+/=` legally, and the browser then
        throws while opening the socket: REST keeps working, so the app looks healthy while robot
        pose, battery and mission state silently stop arriving, and the reconnect never recovers.
        """
        if token is not None and not _TOKEN_CHARS.fullmatch(token):
            raise ValueError(
                "auth_bearer_token must use RFC 7230 token characters "
                "(A-Za-z0-9 and !#$%&'*+-.^_`|~), because it rides the WebSocket subprotocol; "
                "prefer `openssl rand -hex 32` over base64"
            )
        return token

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _accept_a_comma_separated_list(cls, origins: object) -> object:
        """Take the form an operator will actually write, not only the JSON one.

        Without this a bare origin and a comma-separated pair are both parse errors that stop the
        process from starting, which is a hard failure for a fleet-control backend over a config
        knob. The JSON form keeps working, so nothing already deployed has to change.
        """
        if not isinstance(origins, str):
            return origins
        text = origins.strip()
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @property
    def database_url_str(self) -> str:
        return str(self.database_url)
