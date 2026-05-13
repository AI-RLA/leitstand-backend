"""SQLAlchemy 2.0 async engine, session factory, and declarative Base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transactional_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session inside a transaction; commit on clean exit, rollback on exception.

    Canonical boundary-owned-transaction seam (ADR 0017 Transactions
    addendum). All driving adapters (FastAPI HTTP via get_db_session,
    Zenoh wrappers in factory.py, future CLI/Celery entry points) use
    this. Application services and outbound adapters never call commit()
    — that responsibility belongs to the boundary.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
