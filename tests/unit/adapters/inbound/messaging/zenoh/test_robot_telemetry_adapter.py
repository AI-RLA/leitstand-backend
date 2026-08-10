"""ZenohRobotTelemetryAdapter wiring: strict proto-JSON parse -> domain model -> bus."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from google.protobuf import json_format
from leitstand.robot.v1 import telemetry_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.robot.robot_telemetry_adapter import (
    ZenohRobotTelemetryAdapter,
)
from leitstand_backend.application.robot_telemetry_service import RobotTelemetryService
from leitstand_backend.infrastructure.event_bus import EventBus

_TS = datetime(2026, 5, 1, 12, 34, 56, tzinfo=timezone.utc)


def _proto_json(msg) -> bytes:
    return json_format.MessageToJson(msg, preserving_proto_field_name=True).encode()


def _pose_payload(lat: float, lon: float, heading_deg: float | None = None) -> bytes:
    pose = telemetry_pb2.Pose(lat=lat, lon=lon)
    pose.timestamp.FromDatetime(_TS)
    if heading_deg is not None:
        pose.heading_deg = heading_deg
    return _proto_json(pose)


def _battery_payload(battery_pct: int, charging: bool) -> bytes:
    battery = telemetry_pb2.Battery(battery_pct=battery_pct, charging=charging)
    battery.timestamp.FromDatetime(_TS)
    return _proto_json(battery)


def _setup_adapter(
    loop: asyncio.AbstractEventLoop,
) -> tuple[MagicMock, list, EventBus, ZenohRobotTelemetryAdapter]:
    session = MagicMock()
    declared: list[tuple[str, callable]] = []

    def fake_declare_subscriber(ke, handler):
        sub = MagicMock()
        declared.append((ke, handler))
        return sub

    session.declare_subscriber.side_effect = fake_declare_subscriber

    bus = EventBus()
    telemetry_uc = RobotTelemetryService(events=bus)
    adapter = ZenohRobotTelemetryAdapter(
        session=session,
        robot_id="robot_test",
        telemetry_use_case=telemetry_uc,
        loop=loop,
    )
    adapter.start()
    return session, declared, bus, adapter


@pytest.mark.asyncio
async def test_pose_event_routed_to_bus() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    queue = bus.subscribe("events/robot/robot_test/pose")
    pose_handler = next(h for ke, h in declared if ke.endswith("/pose"))

    sample = MagicMock()
    sample.payload.to_bytes.return_value = _pose_payload(50.78, 7.18, 87.5)
    pose_handler(sample)

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["topic"] == "events/robot/robot_test/pose"
    # The bus carries the in-house domain Pose (frame pinned to wgs84 by the mapper).
    assert event["payload"]["lat"] == 50.78
    assert event["payload"]["frame"] == "wgs84"
    assert event["payload"]["heading_deg"] == 87.5


@pytest.mark.asyncio
async def test_battery_routed() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    bq = bus.subscribe("events/robot/robot_test/battery")
    battery_handler = next(h for ke, h in declared if ke.endswith("/battery"))

    bsample = MagicMock()
    bsample.payload.to_bytes.return_value = _battery_payload(87, False)
    battery_handler(bsample)

    bevent = await asyncio.wait_for(bq.get(), timeout=1.0)
    assert bevent["payload"]["battery_pct"] == 87


@pytest.mark.asyncio
async def test_invalid_payload_does_not_publish() -> None:
    loop = asyncio.get_running_loop()
    _, declared, bus, _ = _setup_adapter(loop)

    queue = bus.subscribe("events/robot/robot_test/pose")
    pose_handler = next(h for ke, h in declared if ke.endswith("/pose"))

    sample = MagicMock()
    sample.payload.to_bytes.return_value = b"not proto-json"
    pose_handler(sample)

    with pytest.raises(Exception):
        await asyncio.wait_for(queue.get(), timeout=0.05)
