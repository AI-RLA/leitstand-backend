"""Alembic environment — reads LEITSTAND_DATABASE_URL from settings."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models so Base.metadata sees them for autogenerate.
# (Models are added to this list as the schema grows.)
import leitstand_backend.adapters.outbound.persistence.postgres.models  # noqa: F401
from leitstand_backend.infrastructure.db import Base
from leitstand_backend.infrastructure.factory import alembic_url_option
from leitstand_backend.infrastructure.settings import Settings

config = context.config
# The ini's logging is for the CLI; embedded in the backend it would replace the process's own.
# disable_existing_loggers stays off because the default silences every logger created before it.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# The embedding backend passes its URL through the attributes; the CLI has only the settings.
database_url = config.attributes.get("database_url") or Settings().database_url_str
config.set_main_option("sqlalchemy.url", alembic_url_option(database_url))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
