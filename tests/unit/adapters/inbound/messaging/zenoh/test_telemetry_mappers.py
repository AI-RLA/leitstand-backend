"""Tests for the proto pose/battery -> in-house domain model mappers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from leitstand.robot.v1 import telemetry_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.robot.telemetry_mappers import (
    battery_from_proto,
    pose_from_proto,
)

_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)


def _pose(**fields) -> telemetry_pb2.Pose:
    pose = telemetry_pb2.Pose(**fields)
    pose.timestamp.FromDatetime(_TS)
    return pose


def test_pose_maps_all_fields() -> None:
    proto = _pose(lat=52.5, lon=13.4)
    proto.heading_deg = 270.0
    proto.horizontal_accuracy_m = 1.5
    pose = pose_from_proto(proto)
    assert (pose.lat, pose.lon) == (52.5, 13.4)
    assert pose.frame == "wgs84"  # pinned by the mapper (not on the wire)
    assert pose.heading_deg == 270.0
    assert pose.horizontal_accuracy_m == 1.5
    assert pose.ts == _TS


def test_pose_absent_optionals_map_to_none() -> None:
    pose = pose_from_proto(_pose(lat=1.0, lon=2.0))
    assert pose.heading_deg is None
    assert pose.horizontal_accuracy_m is None


def test_pose_missing_timestamp_raises() -> None:
    with pytest.raises(ValueError):
        pose_from_proto(telemetry_pb2.Pose(lat=1.0, lon=2.0))  # no timestamp


def test_pose_out_of_range_rejected_by_domain() -> None:
    with pytest.raises(Exception):
        pose_from_proto(_pose(lat=200.0, lon=2.0))


def test_pose_nan_rejected_by_domain() -> None:
    with pytest.raises(Exception):
        pose_from_proto(_pose(lat=float("nan"), lon=2.0))


def test_battery_maps_fields() -> None:
    proto = telemetry_pb2.Battery(battery_pct=80, charging=True)
    proto.timestamp.FromDatetime(_TS)
    battery = battery_from_proto(proto)
    assert battery.battery_pct == 80
    assert battery.charging is True
    assert battery.ts == _TS


def test_battery_missing_timestamp_raises() -> None:
    with pytest.raises(ValueError):
        battery_from_proto(telemetry_pb2.Battery(battery_pct=50, charging=False))
