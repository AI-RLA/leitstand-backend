"""Unit tests for PostgresSiteRepositoryAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from leitstand_backend.adapters.outbound.persistence.postgres.site_repository_adapter import (
    PostgresSiteRepositoryAdapter,
)


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_deleted():
    session = AsyncMock()
    delete_result = AsyncMock()
    delete_result.rowcount = 1
    session.execute.return_value = delete_result

    adapter = PostgresSiteRepositoryAdapter(session=session)

    assert await adapter.delete(uuid4()) is True


@pytest.mark.asyncio
async def test_delete_returns_false_when_row_absent():
    session = AsyncMock()
    delete_result = AsyncMock()
    delete_result.rowcount = 0
    session.execute.return_value = delete_result

    adapter = PostgresSiteRepositoryAdapter(session=session)

    assert await adapter.delete(uuid4()) is False
