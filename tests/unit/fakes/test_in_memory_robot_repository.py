from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from leitstand_backend.domain.model.robot import Metadata
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository


def _meta(robot_id: str) -> Metadata:
    return Metadata(id=robot_id)


@pytest.mark.asyncio
async def test_record_online_inserts_new_robot() -> None:
    repo = InMemoryRobotRepository()
    robot = await repo.record_online("scout_mini_4", _meta("scout_mini_4"))

    assert robot.id == "scout_mini_4"
    assert robot.metadata.id == "scout_mini_4"
    assert robot.online is True
    assert isinstance(robot.last_seen, datetime)
    assert await repo.get("scout_mini_4") == robot


@pytest.mark.asyncio
async def test_list_returns_sorted_by_id() -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("scout_mini_4", _meta("scout_mini_4"))
    await repo.record_online("scout_mini_1", _meta("scout_mini_1"))
    await repo.record_online("scout_mini_2", _meta("scout_mini_2"))

    ids = [r.id for r in await repo.list()]
    assert ids == ["scout_mini_1", "scout_mini_2", "scout_mini_4"]


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown() -> None:
    repo = InMemoryRobotRepository()
    assert await repo.get("ghost") is None


@pytest.mark.asyncio
async def test_record_offline_keeps_metadata() -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("scout_mini_4", _meta("scout_mini_4"))
    updated = await repo.record_offline("scout_mini_4")

    assert updated is not None
    assert updated.online is False
    assert updated.metadata.id == "scout_mini_4"
    cached = await repo.get("scout_mini_4")
    assert cached is not None and cached.online is False


@pytest.mark.asyncio
async def test_record_offline_unknown_is_tolerated() -> None:
    repo = InMemoryRobotRepository()
    assert await repo.record_offline("ghost") is None


@pytest.mark.asyncio
async def test_record_offline_is_idempotent() -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("r", _meta("r"))
    first = await repo.record_offline("r")
    second = await repo.record_offline("r")
    assert first is not None and second is not None
    assert first.online is False
    assert second.online is False


@pytest.mark.asyncio
async def test_record_online_brings_offline_robot_back_online() -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("r", _meta("r"))
    await repo.record_offline("r")
    refreshed = await repo.record_online("r", _meta("r"))
    assert refreshed.online is True


@pytest.mark.asyncio
async def test_record_online_logs_conflict_when_already_online(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("r", _meta("r"))
    with caplog.at_level(
        logging.WARNING,
        logger="tests.fakes.in_memory_robot_repository",
    ):
        await repo.record_online("r", _meta("r"))
    assert any("conflict" in rec.message.lower() for rec in caplog.records)


@pytest.mark.asyncio
async def test_last_seen_advances_on_record_online() -> None:
    repo = InMemoryRobotRepository()
    first = await repo.record_online("r", _meta("r"))
    artificial_past = first.last_seen - timedelta(seconds=1)
    repo._robots["r"] = first.model_copy(  # type: ignore[attr-defined]
        update={"last_seen": artificial_past}
    )
    second = await repo.record_online("r", _meta("r"))
    assert second.last_seen > artificial_past
    assert second.last_seen.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_mark_all_offline() -> None:
    repo = InMemoryRobotRepository()
    await repo.record_online("r1", _meta("r1"))
    await repo.record_online("r2", _meta("r2"))
    await repo.mark_all_offline()
    assert all(not r.online for r in await repo.list())
