"""Zenoh-backed MissionDispatcher.

Dispatches runs to robots via queryables (proto-canonical JSON payloads);
pause/resume via PUT keys. Cancel, pause and resume return nothing useful: their
effect shows up on the state channel.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import structlog
import zenoh
from google.protobuf import json_format
from leitstand.robot.v1 import mission_pb2

from leitstand_backend.adapters.outbound.messaging.zenoh.mission.mission_mappers import (
    cancel_to_proto,
    dispatch_request_to_proto,
    dispatch_response_from_proto,
)
from leitstand_backend.domain.errors import MissionDispatchTimeout, MissionRejectedByRobot
from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher

logger = structlog.get_logger(__name__)

_DISPATCH_KEY = "leitstand/robot/{robot_id}/mission/_action/send_goal"
_CANCEL_KEY = "leitstand/robot/{robot_id}/mission/_action/cancel_goal"
_PAUSE_KEY = "leitstand/robot/{robot_id}/instant/pause"
_RESUME_KEY = "leitstand/robot/{robot_id}/instant/resume"

_WIRE_ENCODING = "application/json;leitstand.robot.v1"
_DISPATCH_TIMEOUT_S = 10.0
_CANCEL_TIMEOUT_S = 5.0


def _to_json(message: Any) -> bytes:
    return json_format.MessageToJson(message, preserving_proto_field_name=True).encode("utf-8")


class ZenohMissionDispatcherAdapter(MissionDispatcher):
    """Serialize a run to proto-JSON, call the robot's queryable, parse the reply."""

    def __init__(self, session: zenoh.Session) -> None:
        self._session = session

    async def dispatch(self, run_id: UUID, stages: list[Stage], robot_id: str) -> None:
        key = _DISPATCH_KEY.format(robot_id=robot_id)
        dispatch_id = str(uuid4())
        payload = _to_json(dispatch_request_to_proto(run_id, stages, dispatch_id))

        logger.info(
            "mission_dispatch_sending",
            run_id=str(run_id),
            robot_id=robot_id,
            dispatch_id=dispatch_id,
        )

        replies = await _get(self._session, key, payload, _DISPATCH_TIMEOUT_S)
        if not replies:
            logger.warning("mission_dispatch_timeout", run_id=str(run_id), robot_id=robot_id)
            raise MissionDispatchTimeout(run_id, robot_id)

        for reply in replies:
            body = _reply_payload_bytes(reply)
            if body is None:
                continue
            try:
                response = json_format.Parse(
                    body, mission_pb2.MissionDispatchResponse(), ignore_unknown_fields=False
                )
            except json_format.ParseError as exc:
                logger.warning(
                    "mission_dispatch_invalid_reply",
                    run_id=str(run_id),
                    robot_id=robot_id,
                    error=str(exc),
                )
                continue
            accepted, reason = dispatch_response_from_proto(response)
            if not accepted:
                raise MissionRejectedByRobot(run_id, robot_id, reason)
            logger.info("mission_dispatch_accepted", run_id=str(run_id), robot_id=robot_id)
            return

        raise MissionDispatchTimeout(run_id, robot_id)

    async def cancel(
        self,
        run_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> None:
        key = _CANCEL_KEY.format(robot_id=robot_id)
        payload = _to_json(cancel_to_proto(run_id, mode))
        logger.info(
            "mission_cancel_sending", run_id=str(run_id), robot_id=robot_id, mode=mode.value
        )
        await _get(self._session, key, payload, _CANCEL_TIMEOUT_S)

    async def pause(self, run_id: UUID, robot_id: str) -> None:
        key = _PAUSE_KEY.format(robot_id=robot_id)
        logger.info("mission_pause_sending", run_id=str(run_id), robot_id=robot_id)
        await _put(self._session, key)

    async def resume(self, run_id: UUID, robot_id: str) -> None:
        key = _RESUME_KEY.format(robot_id=robot_id)
        logger.info("mission_resume_sending", run_id=str(run_id), robot_id=robot_id)
        await _put(self._session, key)


async def _get(
    session: zenoh.Session,
    key: str,
    payload: bytes,
    timeout: float,
) -> list[Any]:
    """Run session.get() in the default thread pool; return the list of replies."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: list(session.get(key, payload=payload, encoding=_WIRE_ENCODING, timeout=timeout)),
    )


async def _put(session: zenoh.Session, key: str) -> None:
    """Run session.put() in the default thread pool (empty instant-action signal)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: session.put(key, b""))


def _reply_payload_bytes(reply: Any) -> bytes | None:
    """Extract bytes payload from a Zenoh reply, or None on error."""
    err = getattr(reply, "err", None)
    if err is not None:
        logger.warning("zenoh_reply_error", error=str(err))
        return None
    ok = getattr(reply, "ok", reply)
    payload = getattr(ok, "payload", None)
    if payload is None:
        return None
    try:
        return bytes(payload.to_bytes())
    except AttributeError:
        try:
            return bytes(payload)
        except Exception:  # noqa: BLE001
            return None
