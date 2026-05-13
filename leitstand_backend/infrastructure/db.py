"""SQLAlchemy 2.0 async engine, session factory, and declarative Base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
