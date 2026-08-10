"""Unit tests for PostgresMissionRepositoryAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from leitstand_backend.adapters.outbound.persistence.postgres.mission_repository_adapter import (
    PostgresMissionRepositoryAdapter,
)
from leitstand_backend.domain.errors import MissionNotFoundError


@pytest.mark.asyncio
async def test_get_status_raises_when_mission_absent():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    adapter = PostgresMissionRepositoryAdapter(session=session)
    with pytest.raises(MissionNotFoundError):
        await adapter.get_status(uuid4())


@pytest.mark.asyncio
async def test_get_assigned_robot_returns_none_when_unassigned():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    adapter = PostgresMissionRepositoryAdapter(session=session)
    assert await adapter.get_assigned_robot(uuid4()) is None
