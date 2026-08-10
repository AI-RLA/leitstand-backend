"""Fleet-wide Zenoh subscriber for robot mission state messages.

Subscribes to ``leitstand/robot/*/mission/state`` (one subscriber,
wildcard). On each sample, extracts the robot_id from the key and
routes the MissionStateMessage to MissionStateUseCase.record().
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

import structlog
import zenoh
from google.protobuf import json_format
from leitstand.robot.v1 import mission_state_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.mission.state_mappers import (
    mission_state_from_proto,
)
from leitstand_backend.ports.inbound.mission_state import (
    MissionStateUseCase,
    RecordMissionStateCommand,
)

logger = structlog.get_logger(__name__)

_STATE_KE = "leitstand/robot/*/mission/state"
_STATE_PREFIX = "leitstand/robot/"
_STATE_SUFFIX = "/mission/state"
_WORKER_JOIN_TIMEOUT_S = 2.0


class ZenohMissionStateAdapter:
    """Fleet-wide subscriber that routes robot state messages to the use case."""

    def __init__(
        self,
        session: zenoh.Session,
        use_case: MissionStateUseCase,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session = session
        self._use_case = use_case
        self._loop = loop
        self._subscriber: Any = None
        self._closed = threading.Event()

    def start(self) -> None:
        if self._subscriber is not None:
            return
        self._subscriber = self._session.declare_subscriber(_STATE_KE, self._handle_sample)
        logger.info("mission_state_subscriber_declared", key=_STATE_KE)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        sub = self._subscriber
        self._subscriber = None
        if sub is not None:
            try:
                sub.undeclare()
            except Exception as e:  # noqa: BLE001
                logger.warning("mission_state_subscriber_undeclare_failed", error=str(e))

    def _handle_sample(self, sample: Any) -> None:
        try:
            key = str(sample.key_expr)
            robot_id = _extract_robot_id(key)
            if robot_id is None:
                logger.debug("mission_state_key_shape_mismatch", key=key)
                return
            payload_bytes = bytes(sample.payload.to_bytes())
            try:
                proto = json_format.Parse(
                    payload_bytes, mission_state_pb2.MissionState(), ignore_unknown_fields=False
                )
                state = mission_state_from_proto(proto)
            except Exception as exc:  # noqa: BLE001
                # A frame the strict parser rejects is unmappable (unknown field/enum);
                # error=str(exc) names the offending field. Drop and alert; never default.
                logger.warning("mission_state_frame_rejected", robot_id=robot_id, error=str(exc))
                return
            command = RecordMissionStateCommand(robot_id=robot_id, state=state)
            fut = asyncio.run_coroutine_threadsafe(self._use_case.record(command), self._loop)
            fut.add_done_callback(_log_future_failure("record_mission_state", robot_id))
        except Exception as e:  # noqa: BLE001
            if not self._closed.is_set():
                logger.exception("mission_state_handler_error", error=str(e))


def _extract_robot_id(key: str) -> str | None:
    if not key.startswith(_STATE_PREFIX) or not key.endswith(_STATE_SUFFIX):
        return None
    inner = key[len(_STATE_PREFIX) : -len(_STATE_SUFFIX)]
    if not inner or "/" in inner:
        return None
    return inner


def _log_future_failure(action: str, robot_id: str):
    def _cb(fut: concurrent.futures.Future) -> None:
        try:
            fut.result()
        except Exception:  # noqa: BLE001
            logger.exception("mission_state_use_case_failed", action=action, robot_id=robot_id)

    return _cb
