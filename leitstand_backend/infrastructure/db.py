"""SQLAlchemy 2.0 async engine, session factory, and declarative Base."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from leitstand_backend.infrastructure.settings import Settings

logger = structlog.get_logger(__name__)

_AFTER_COMMIT_KEY = "after_commit_callbacks"


def register_after_commit(session: AsyncSession, callback: Callable[[], None]) -> None:
    """Queue a zero-arg callback to run after this session's transaction commits (FIFO).

    Lets a producer defer a side effect (e.g. publishing a bus event) until its write is
    durable. The callback runs in ``run_after_commit_callbacks``, invoked by the commit
    boundary; it is discarded if the transaction rolls back.
    """
    session.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)


def run_after_commit_callbacks(session: AsyncSession) -> None:
    """Run and clear the session's queued after-commit callbacks, FIFO. Never raises."""
    for callback in session.info.pop(_AFTER_COMMIT_KEY, ()):
        try:
            callback()
        except Exception:  # noqa: BLE001 - one failed callback must not drop the rest
            logger.exception("after_commit_callback_failed")


class Base(DeclarativeBase):
    """Declarative base for all ORM-mapped tables."""


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url_str,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    """Lightweight connectivity probe. Raises OperationalError on failure.

    asyncpg leaks bare ConnectionRefusedError when the TCP socket is
    refused (server stopped). Normalize so callers only need to handle
    OperationalError.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OperationalError:
        raise
    except (ConnectionError, OSError) as e:
        raise OperationalError("ping failed", None, e) from e


@asynccontextmanager
async def transactional_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session inside a transaction. Commit on clean exit, rollback on exception.

    Application services and outbound adapters never call commit().
    That responsibility belongs to this boundary.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        else:
            run_after_commit_callbacks(session)
