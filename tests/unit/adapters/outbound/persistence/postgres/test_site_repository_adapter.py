"""Unit tests for PostgresSiteRepositoryAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from leitstand_backend.adapters.outbound.persistence.postgres.site_repository_adapter import (
    PostgresSiteRepositoryAdapter,
)
from leitstand_backend.domain.errors import SiteInUse


@pytest.mark.asyncio
async def test_delete_raises_site_in_use_when_blocking_missions_present():
    session = AsyncMock()
    adapter = PostgresSiteRepositoryAdapter(session=session)
    site_id = uuid4()
    blocking_id = uuid4()

    with patch.object(adapter, "_find_blocking_missions", return_value=[blocking_id]):
        with pytest.raises(SiteInUse) as excinfo:
            await adapter.delete(site_id)

    assert excinfo.value.site_id == site_id
    assert excinfo.value.blocking_mission_ids == [blocking_id]


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_deleted_and_no_blockers():
    session = AsyncMock()
    delete_result = AsyncMock()
    delete_result.rowcount = 1
    session.execute.return_value = delete_result

    adapter = PostgresSiteRepositoryAdapter(session=session)

    with patch.object(adapter, "_find_blocking_missions", return_value=[]):
        deleted = await adapter.delete(uuid4())

    assert deleted is True


@pytest.mark.asyncio
async def test_delete_returns_false_when_row_absent():
    session = AsyncMock()
    delete_result = AsyncMock()
    delete_result.rowcount = 0
    session.execute.return_value = delete_result

    adapter = PostgresSiteRepositoryAdapter(session=session)

    with patch.object(adapter, "_find_blocking_missions", return_value=[]):
        assert await adapter.delete(uuid4()) is False
