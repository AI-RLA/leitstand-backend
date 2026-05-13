"""Unit tests for PostgresRobotRepositoryAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from leitstand_backend.adapters.outbound.persistence.postgres.robot_repository_adapter import (
    PostgresRobotRepositoryAdapter,
)


@pytest.mark.asyncio
async def test_save_telemetry_raises_on_unknown_kind() -> None:
    adapter = PostgresRobotRepositoryAdapter(session=AsyncMock())
    with pytest.raises(ValueError, match="unknown telemetry kind"):
        await adapter.save_telemetry("r1", "nonsense", {})
