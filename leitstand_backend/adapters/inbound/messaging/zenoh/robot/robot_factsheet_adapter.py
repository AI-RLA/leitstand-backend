"""Zenoh-based robot factsheet fetcher.

Called synchronously from a thread pool whenever a robot comes online.
Queries the robot's factsheet queryable, validates the payload, and
bridges the result to the asyncio event loop via the use case.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

import structlog
import zenoh
from google.protobuf import json_format
from leitstand.robot.v1 import factsheet_pb2

from leitstand_backend.adapters.inbound.messaging.zenoh.robot.factsheet_mappers import (
    factsheet_from_proto,
)
from leitstand_backend.ports.inbound.robot_factsheet import (
    RecordRobotFactsheetCommand,
    RobotFactsheetUseCase,
)

logger = structlog.get_logger(__name__)

_FACTSHEET_KEY = "leitstand/robot/{robot_id}/factsheet"
_QUERY_TIMEOUT_S = 3.0


class ZenohRobotFactsheetAdapter:
    """Fetches and records the factsheet for one robot on request."""

    def __init__(
        self,
        session: zenoh.Session,
        use_case: RobotFactsheetUseCase,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session = session
        self._use_case = use_case
        self._loop = loop

    def fetch_and_record(self, robot_id: str) -> None:
        """Query the robot's factsheet queryable and record it.

        Designed to run in a thread pool (blocking Zenoh call). Safe
        to call from the liveliness worker thread.
        """
        key = _FACTSHEET_KEY.format(robot_id=robot_id)
        try:
            replies = self._session.get(key, timeout=_QUERY_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            logger.warning("factsheet_query_failed", robot_id=robot_id, error=str(e))
            return

        for reply in replies:
            payload = _reply_payload_bytes(reply)
            if payload is None:
                continue
            try:
                # Strict parse: a non-v1 factsheet has unknown fields and is rejected.
                proto = json_format.Parse(
                    payload, factsheet_pb2.Factsheet(), ignore_unknown_fields=False
                )
                factsheet = factsheet_from_proto(proto, robot_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("factsheet_parse_failed", robot_id=robot_id, error=str(e))
                continue

            command = RecordRobotFactsheetCommand(factsheet=factsheet)
            fut = asyncio.run_coroutine_threadsafe(self._use_case.record(command), self._loop)
            fut.add_done_callback(_log_future_failure("record_factsheet", robot_id))
            logger.info("factsheet_fetched", robot_id=robot_id)
            return

        logger.warning("factsheet_no_reply", robot_id=robot_id, timeout_s=_QUERY_TIMEOUT_S)


def _reply_payload_bytes(reply: Any) -> bytes | None:
    err = getattr(reply, "err", None)
    if err is not None:
        logger.warning("factsheet_reply_error", error=str(err))
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


def _log_future_failure(action: str, robot_id: str):
    def _cb(fut: concurrent.futures.Future) -> None:
        try:
            fut.result()
        except Exception:  # noqa: BLE001
            logger.exception("factsheet_use_case_failed", action=action, robot_id=robot_id)

    return _cb
