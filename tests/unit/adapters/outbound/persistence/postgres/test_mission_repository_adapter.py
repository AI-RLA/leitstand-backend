"""Unit tests for PostgresMissionRepositoryAdapter against a mocked session."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from leitstand_backend.adapters.outbound.persistence.postgres.mission_repository_adapter import (
    PostgresMissionRepositoryAdapter,
)


@pytest.mark.asyncio
async def test_get_returns_none_when_mission_absent():
    session = AsyncMock()
    session.get.return_value = None

    adapter = PostgresMissionRepositoryAdapter(session=session)
    assert await adapter.get(uuid4()) is None


@pytest.mark.asyncio
async def test_missions_referencing_site_reads_the_stage_rows():
    session = AsyncMock()
    result = MagicMock()
    mission_id = uuid4()
    result.all.return_value = [(mission_id,)]
    session.execute.return_value = result

    adapter = PostgresMissionRepositoryAdapter(session=session)
    assert await adapter.missions_referencing_site(uuid4()) == [mission_id]
