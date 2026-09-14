"""The alembic config must carry a password that URL-encoding turned into `%xx` escapes."""

from __future__ import annotations

from unittest.mock import patch

from alembic.config import Config as AlembicConfig

from leitstand_backend.infrastructure import factory

URL = "postgresql+asyncpg://leitstand:p%40ss%2Fw%25rd@postgres:5432/leitstand"


def test_the_escaped_url_survives_the_ini_parser() -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("sqlalchemy.url", factory.alembic_url_option(URL))
    assert cfg.get_main_option("sqlalchemy.url") == URL


def test_the_app_url_is_handed_to_env_py_unchanged() -> None:
    captured: dict[str, AlembicConfig] = {}

    def fake_upgrade(cfg: AlembicConfig, revision: str) -> None:
        captured["cfg"] = cfg

    with patch.object(factory.alembic_command, "upgrade", fake_upgrade):
        factory._run_migrations(URL)

    assert captured["cfg"].attributes["database_url"] == URL
