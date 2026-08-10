"""Per-robot Zenoh subscriber: routes sensor telemetry (pose, battery) to its port.

One instance per online robot. Runs all handlers on Zenoh's runtime
thread; handlers must never raise.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

import structlog
import zenoh
from google.protobuf import json_format
from leitstand.robot.v1 import telemetry_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.robot.telemetry_mappers import (
    battery_from_proto,
    pose_from_proto,
)
from leitstand_backend.ports.inbound.robot_telemetry import (
    RecordBatteryCommand,
    RecordPoseCommand,
    RobotTelemetryUseCase,
)

logger = structlog.get_logger(__name__)


class ZenohRobotTelemetryAdapter:
    """Subscribe to a robot's pose + battery Zenoh keys and route them to the telemetry port."""

    def __init__(
        self,
        session: zenoh.Session,
        robot_id: str,
        telemetry_use_case: RobotTelemetryUseCase,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session = session
        self._robot_id = robot_id
        self._telemetry_uc = telemetry_use_case
        self._loop = loop
        self._subscribers: list[Any] = []
        self._closed = threading.Event()

    def start(self) -> None:
        for kind, handler in (
            (
                "pose",
                self._make_telemetry_handler(
                    "pose", telemetry_pb2.Pose, pose_from_proto, RecordPoseCommand, "record_pose"
                ),
            ),
            (
                "battery",
                self._make_telemetry_handler(
                    "battery",
                    telemetry_pb2.Battery,
                    battery_from_proto,
                    RecordBatteryCommand,
                    "record_battery",
                ),
            ),
        ):
            ke = f"leitstand/robot/{self._robot_id}/{kind}"
            sub = self._session.declare_subscriber(ke, handler)
            self._subscribers.append(sub)
        logger.info("robot_data_subscribed", robot_id=self._robot_id)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for sub in self._subscribers:
            try:
                sub.undeclare()
            except Exception:  # noqa: BLE001
                pass
        self._subscribers.clear()
        logger.info("robot_data_unsubscribed", robot_id=self._robot_id)

    def _make_telemetry_handler(
        self, kind: str, proto_type, mapper, CommandClass, method_name: str
    ):
        def handler(sample: Any) -> None:
            try:
                payload = bytes(sample.payload.to_bytes())
                frame = json_format.Parse(payload, proto_type(), ignore_unknown_fields=False)
                event = mapper(frame)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "telemetry_decode_error", robot_id=self._robot_id, kind=kind, error=str(e)
                )
                return
            command = CommandClass(robot_id=self._robot_id, **{kind: event})
            method = getattr(self._telemetry_uc, method_name)
            fut = asyncio.run_coroutine_threadsafe(method(command), self._loop)
            fut.add_done_callback(_log_future_failure(method_name, self._robot_id))

        return handler


def _log_future_failure(action: str, robot_id: str):
    def _cb(fut: concurrent.futures.Future) -> None:
        try:
            fut.result()
        except Exception:  # noqa: BLE001
            logger.exception("data_use_case_failed", action=action, robot_id=robot_id)

    return _cb
