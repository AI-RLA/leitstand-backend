"""Typed env-based settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def database_url_str(self) -> str:
        return str(self.database_url)
