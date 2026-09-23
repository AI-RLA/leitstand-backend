"""Fixtures for the ``integration`` marker: a throwaway Postgres database, migrated to head.

The session creates its own database on the server in LEITSTAND_TEST_DATABASE_URL and drops it at
the end, so the tests never write to a database they did not create. An unreachable server fails
the run instead of skipping it, because a run that silently skips every test reads as a pass.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from leitstand_backend.infrastructure.db import create_engine, create_session_factory
from leitstand_backend.infrastructure.factory import _run_migrations
from leitstand_backend.infrastructure.settings import Settings

# Only the server and login of this URL are used, never its database.
_DEFAULT_SERVER_URL = "postgresql+asyncpg://leitstand:leitstand@localhost:5432/postgres"
# A fixed name, so a run that died before its teardown is cleaned up by the next one.
_TEST_DATABASE = "leitstand_test"


async def _recreate(server: URL, *, create: bool) -> None:
    # From the maintenance database, because a database cannot be dropped while connected to it.
    dsn = server.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)')
        if create:
            await connection.execute(f'CREATE DATABASE "{_TEST_DATABASE}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    server = make_url(os.environ.get("LEITSTAND_TEST_DATABASE_URL", _DEFAULT_SERVER_URL))
    try:
        asyncio.run(_recreate(server, create=True))
    except Exception as exc:  # noqa: BLE001 - surfaced as a failure with guidance
        pytest.fail(
            "integration tests need a Postgres server whose login may create databases, at "
            "LEITSTAND_TEST_DATABASE_URL (default: the compose stack's Postgres on localhost). "
            f"Setting up {_TEST_DATABASE!r} failed: {exc}"
        )
    url = server.set(database=_TEST_DATABASE).render_as_string(hide_password=False)
    _run_migrations(url)
    yield url
    asyncio.run(_recreate(server, create=False))


@pytest_asyncio.fixture
async def db_engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(Settings(database_url=test_database_url))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest_asyncio.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session whose work is rolled back, so one test's rows never reach the next."""
    async with db_session_factory() as session:
        yield session
        await session.rollback()
