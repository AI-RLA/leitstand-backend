"""Named-field mappers from the proto pose/battery frames to the in-house models.

The anti-corruption layer (ACL) for robot sensor telemetry: each field is mapped
explicitly, and the in-house pydantic model re-validates bounds the proto-JSON
parser admits (e.g. NaN) so an out-of-range or non-finite value is rejected on
construction and the calling adapter drops the frame.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google.protobuf import timestamp_pb2
from leitstand.robot.v1 import telemetry_pb2

from leitstand_backend.domain.model.robot.telemetry import Battery, Pose


def _utc(ts: timestamp_pb2.Timestamp) -> datetime:
    return ts.ToDatetime(tzinfo=timezone.utc)


def pose_from_proto(pose: telemetry_pb2.Pose) -> Pose:
    """Rebuild the in-house Pose from a proto frame; raise if the timestamp is missing."""
    if not pose.HasField("timestamp"):
        raise ValueError("pose frame is missing its timestamp")
    return Pose(
        ts=_utc(pose.timestamp),
        lat=pose.lat,
        lon=pose.lon,
        heading_deg=pose.heading_deg if pose.HasField("heading_deg") else None,
        horizontal_accuracy_m=(
            pose.horizontal_accuracy_m if pose.HasField("horizontal_accuracy_m") else None
        ),
    )


def battery_from_proto(battery: telemetry_pb2.Battery) -> Battery:
    """Rebuild the in-house Battery from a proto frame; raise if the timestamp is missing."""
    if not battery.HasField("timestamp"):
        raise ValueError("battery frame is missing its timestamp")
    return Battery(
        ts=_utc(battery.timestamp),
        battery_pct=battery.battery_pct,
        charging=battery.charging,
    )
