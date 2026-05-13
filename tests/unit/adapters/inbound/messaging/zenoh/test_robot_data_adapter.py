"""ZenohRobotDataAdapter wiring + Pydantic validation."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from leitstand_backend.adapters.inbound.messaging.zenoh.robot_data_adapter import (
    ZenohRobotDataAdapter,
)
from leitstand_backend.application.robot_state_service import RobotStateService
from leitstand_backend.application.robot_telemetry_service import RobotTelemetryService
from leitstand_backend.infrastructure.event_bus import EventBus


def _setup_adapter(
    loop: asyncio.AbstractEventLoop,
) -> tuple[MagicMock, list, EventBus, ZenohRobotDataAdapter]:
    session = MagicMock()
    declared: list[tuple[str, callable]] = []

    def fake_declare_subscriber(ke, handler):
        sub = MagicMock()
        declared.append((ke, handler))
        return sub

    session.declare_subscriber.side_effect = fake_declare_subscriber

    bus = EventBus()
    telemetry_uc = RobotTelemetryService(events=bus)
    state_uc = RobotStateService(events=bus)
    adapter = ZenohRobotDataAdapter(
        session=session,
        robot_id="robot_test",
        telemetry_use_case=telemetry_uc,
        state_use_case=state_uc,
        loop=loop,
    )
    adapter.start()
    return session, declared, bus, adapter


@pytest.mark.asyncio
async def test_pose_event_routed_to_bus() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    queue = bus.subscribe("events.robot/robot_test/pose")
    pose_handler = next(h for ke, h in declared if ke.endswith("/pose"))

    sample = MagicMock()
    sample.payload.to_bytes.return_value = json.dumps(
        {
            "ts": "2026-05-01T12:34:56Z",
            "frame": "wgs84",
            "lat": 50.78,
            "lon": 7.18,
            "heading_deg": 87.5,
        }
    ).encode()
    pose_handler(sample)

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["topic"] == "events.robot/robot_test/pose"
    assert event["payload"]["lat"] == 50.78
    assert event["payload"]["frame"] == "wgs84"


@pytest.mark.asyncio
async def test_battery_and_state_routed() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    bq = bus.subscribe("events.robot/robot_test/battery")
    sq = bus.subscribe("events.robot/robot_test/state")

    battery_handler = next(h for ke, h in declared if ke.endswith("/battery"))
    state_handler = next(h for ke, h in declared if ke.endswith("/state"))

    bsample = MagicMock()
    bsample.payload.to_bytes.return_value = json.dumps(
        {"ts": "2026-05-01T12:34:56Z", "battery_pct": 87, "charging": False}
    ).encode()
    battery_handler(bsample)

    ssample = MagicMock()
    ssample.payload.to_bytes.return_value = json.dumps(
        {"ts": "2026-05-01T12:34:56Z", "status": "active", "task": "Test"}
    ).encode()
    state_handler(ssample)

    bevent = await asyncio.wait_for(bq.get(), timeout=1.0)
    sevent = await asyncio.wait_for(sq.get(), timeout=1.0)
    assert bevent["payload"]["battery_pct"] == 87
    assert sevent["payload"]["status"] == "active"


@pytest.mark.asyncio
async def test_invalid_payload_does_not_publish() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    queue = bus.subscribe("events.robot/robot_test/pose")
    pose_handler = next(h for ke, h in declared if ke.endswith("/pose"))

    sample = MagicMock()
    sample.payload.to_bytes.return_value = b"not json"
    pose_handler(sample)

    with pytest.raises(Exception):
        await asyncio.wait_for(queue.get(), timeout=0.05)
