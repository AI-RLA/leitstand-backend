"""Unit tests for RobotStateService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leitstand_backend.application.robot_state_service import RobotStateService
from leitstand_backend.domain.model.telemetry import RobotState
from leitstand_backend.ports.inbound.robot_state import RecordStateCommand
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_record_state_publishes_latched_topic_and_fanout() -> None:
    pub = InMemoryEventPublisher()
    svc = RobotStateService(events=pub)
    state = RobotState(ts=_NOW, status="active", task="mow")

    await svc.record_state(RecordStateCommand(robot_id="r1", state=state))

    latched_topics = [t for t, _, latch in pub.published if latch]
    assert "events.robot/r1/state" in latched_topics
    latched_payloads = {t: p for t, p, latch in pub.published if latch}
    assert latched_payloads["events.robot/r1/state"]["status"] == "active"

    fanout_topics = [t for t, _, latch in pub.published if not latch]
    assert "events.robot/r1" in fanout_topics
