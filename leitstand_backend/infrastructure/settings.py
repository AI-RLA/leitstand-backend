"""Typed env-based settings."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

# Only the directories that exist, because the settings source warns about each that does not;
# later entries override earlier ones.
_SECRET_DIRS = [d for d in ("/run/secrets", "secrets") if Path(d).is_dir()] or None

# The alphabet shared by a WebSocket subprotocol (RFC 7230 token) and an HTTP bearer (RFC 6750
# b64token), which also keeps `$`, `"` and `\` out of the nginx directive the proxy renders it into.
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9._~+-]+")


class Settings(BaseSettings):
    # Precedence, highest first: environment, .env, secret file, field default.
    model_config = SettingsConfigDict(
        env_prefix="LEITSTAND_",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=_SECRET_DIRS,
        extra="ignore",
        # A rejected secret must not be echoed back in the validation error on stdout.
        hide_input_in_errors=True,
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
    db_password: SecretStr | None = Field(default=None)

    # LLM inference (any OpenAI-compatible host; base_url + model select the provider). A soft
    # dependency: it runs on a separate box and must never gate boot, liveness or readiness.
    llm_base_url: str = Field(default="http://localhost:8000/v1")
    llm_api_key: SecretStr | None = Field(default=None)
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
    auth_bearer_token: SecretStr | None = Field(default=None)
    # NoDecode because a list-typed variable is otherwise JSON-decoded by the settings source
    # itself, which fails before any validator runs and takes the process down with it.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("auth_bearer_token", "llm_api_key", "db_password", mode="before")
    @classmethod
    def _a_blank_secret_is_an_absent_one(cls, value: object) -> object:
        """Read an empty secret file as unset.

        Compose refuses to start without the file, so an unset secret is an empty one.
        """
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("auth_bearer_token")
    @classmethod
    def _token_can_ride_a_websocket_subprotocol(cls, token: SecretStr | None) -> SecretStr | None:
        """Refuse a token the browser cannot send, rather than losing the live fleet stream to it.

        The WebSocket handshake carries the token as a subprotocol, which may only hold RFC 7230
        token characters; with a base64 secret the browser throws while opening the socket while
        REST keeps working, so live fleet data would stop without an error.
        """
        if token is not None and not _TOKEN_CHARS.fullmatch(token.get_secret_value()):
            raise ValueError(
                "auth_bearer_token must use only A-Za-z0-9 and ._~+- (RFC 7230 token characters "
                "that are also RFC 6750 bearer characters), because it rides the WebSocket "
                "subprotocol; prefer `openssl rand -hex 32` over base64"
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
        """Return the connection URL, inserting ``db_password`` when the URL carries none.

        An explicit password in the URL wins, which is how the tooling points at a scratch database.
        """
        url = make_url(str(self.database_url))
        if url.password is None and self.db_password is not None:
            url = url.set(password=self.db_password.get_secret_value())
        return url.render_as_string(hide_password=False)
