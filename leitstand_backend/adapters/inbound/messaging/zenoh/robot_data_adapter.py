"""Per-robot Zenoh subscriber: routes sensor telemetry and state to their ports.

One instance per online robot. Runs all handlers on Zenoh's runtime
thread; handlers must never raise.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from typing import Any

import structlog
import zenoh

from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState
from leitstand_backend.ports.inbound.robot_state import RecordStateCommand, RobotStateUseCase
from leitstand_backend.ports.inbound.robot_telemetry import (
    RecordBatteryCommand,
    RecordPoseCommand,
    RobotTelemetryUseCase,
)

logger = structlog.get_logger(__name__)


class ZenohRobotDataAdapter:
    """Subscribes to all leitstand Zenoh keys for one robot.

    Routes pose/battery to RobotTelemetryUseCase and state to
    RobotStateUseCase. The two ports are separate because sensor
    telemetry and operational state have different semantics and
    will evolve independently — but the adapter stays one class
    because both use the same transport, the same robot lifecycle,
    and are never started or stopped independently.
    """

    def __init__(
        self,
        session: zenoh.Session,
        robot_id: str,
        telemetry_use_case: RobotTelemetryUseCase,
        state_use_case: RobotStateUseCase,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session = session
        self._robot_id = robot_id
        self._telemetry_uc = telemetry_use_case
        self._state_uc = state_use_case
        self._loop = loop
        self._subscribers: list[Any] = []
        self._closed = threading.Event()

    def start(self) -> None:
        for kind, handler in (
            ("pose", self._make_telemetry_handler("pose", Pose, RecordPoseCommand, "record_pose")),
            (
                "battery",
                self._make_telemetry_handler(
                    "battery", Battery, RecordBatteryCommand, "record_battery"
                ),
            ),
            ("state", self._handle_state),
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

    def _make_telemetry_handler(self, kind: str, model, CommandClass, method_name: str):
        def handler(sample: Any) -> None:
            try:
                data = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
                event = model.model_validate(data)
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

    def _handle_state(self, sample: Any) -> None:
        try:
            data = json.loads(bytes(sample.payload.to_bytes()).decode("utf-8"))
            state = RobotState.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("state_decode_error", robot_id=self._robot_id, error=str(e))
            return
        command = RecordStateCommand(robot_id=self._robot_id, state=state)
        fut = asyncio.run_coroutine_threadsafe(self._state_uc.record_state(command), self._loop)
        fut.add_done_callback(_log_future_failure("record_state", self._robot_id))


def _log_future_failure(action: str, robot_id: str):
    def _cb(fut: concurrent.futures.Future) -> None:
        try:
            fut.result()
        except Exception:  # noqa: BLE001
            logger.exception("data_use_case_failed", action=action, robot_id=robot_id)

    return _cb
