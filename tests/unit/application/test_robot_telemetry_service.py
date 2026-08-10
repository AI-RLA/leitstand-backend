"""Unit tests for RobotTelemetryService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leitstand_backend.application.robot_telemetry_service import RobotTelemetryService
from leitstand_backend.domain.model.robot.telemetry import Battery, Pose
from leitstand_backend.ports.inbound.robot_telemetry import (
    RecordBatteryCommand,
    RecordPoseCommand,
)
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_record_pose_publishes_latched_topic_and_fanout() -> None:
    pub = InMemoryEventPublisher()
    svc = RobotTelemetryService(events=pub)
    pose = Pose(ts=_NOW, lat=50.78, lon=7.18, heading_deg=90.0)

    await svc.record_pose(RecordPoseCommand(robot_id="r1", pose=pose))

    latched_topics = [t for t, _, latch in pub.published if latch]
    assert "events/robot/r1/pose" in latched_topics
    fanout_topics = [t for t, _, latch in pub.published if not latch]
    assert "events/robot/r1" in fanout_topics


@pytest.mark.asyncio
async def test_record_battery_publishes_latched_topic() -> None:
    pub = InMemoryEventPublisher()
    svc = RobotTelemetryService(events=pub)
    battery = Battery(ts=_NOW, battery_pct=80, charging=False)

    await svc.record_battery(RecordBatteryCommand(robot_id="r1", battery=battery))

    latched_topics = [t for t, _, latch in pub.published if latch]
    assert "events/robot/r1/battery" in latched_topics
    latched_payloads = {t: p for t, p, latch in pub.published if latch}
    assert latched_payloads["events/robot/r1/battery"]["battery_pct"] == 80
