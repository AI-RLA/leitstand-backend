"""Unit tests for RobotConnectivityService."""

from __future__ import annotations

import pytest

from leitstand_backend.application.robot_connectivity_service import (
    RobotConnectivityService,
)
from leitstand_backend.domain.model.robot import Metadata
from leitstand_backend.ports.inbound.robot_connectivity import (
    RecordOfflineCommand,
    RecordOnlineCommand,
)
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository

_META = Metadata(id="r1")


@pytest.mark.asyncio
async def test_record_online_stores_robot_and_publishes_event() -> None:
    repo = InMemoryRobotRepository()
    pub = InMemoryEventPublisher()
    svc = RobotConnectivityService(repo=repo, events=pub)

    robot = await svc.record_online(RecordOnlineCommand(robot_id="r1", metadata=_META))

    assert robot.online
    assert robot.id == "r1"
    stored = await repo.get("r1")
    assert stored is not None and stored.online
    registry_events = [p for t, p, _ in pub.published if t == "events.registry"]
    assert len(registry_events) == 1
    assert registry_events[0] == {"type": "robot.online", "robot_id": "r1"}


@pytest.mark.asyncio
async def test_record_offline_marks_robot_offline_and_publishes_event() -> None:
    repo = InMemoryRobotRepository()
    pub = InMemoryEventPublisher()
    svc = RobotConnectivityService(repo=repo, events=pub)

    await svc.record_online(RecordOnlineCommand(robot_id="r1", metadata=_META))
    pub.published.clear()

    await svc.record_offline(RecordOfflineCommand(robot_id="r1"))

    stored = await repo.get("r1")
    assert stored is not None and not stored.online
    offline_events = [p for t, p, _ in pub.published if t == "events.registry"]
    assert len(offline_events) == 1
    assert offline_events[0]["type"] == "robot.offline"
    # telemetry snapshots are preserved (unlatch removed) so last-known state
    # survives for page-refresh and backend-restart scenarios
    assert pub.unlatched == []


@pytest.mark.asyncio
async def test_record_offline_unknown_robot_returns_none() -> None:
    svc = RobotConnectivityService(repo=InMemoryRobotRepository(), events=InMemoryEventPublisher())
    result = await svc.record_offline(RecordOfflineCommand(robot_id="ghost"))
    assert result is None
