"""Zenoh-side presence tracker.

Owns the liveliness subscription on
``leitstand/robot/*/online`` and the metadata queries it
triggers.

The subscriber uses the channel-handler overload of
``Liveliness.declare_subscriber`` (default FIFO channel,
iterated in a worker thread). This matches the official
zenoh-python ``z_sub_liveliness`` example and is the form for
which ``history=True`` reliably replays already-declared tokens.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import threading
from typing import Any

import structlog
import zenoh
from zenoh import SampleKind

from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.ports.inbound.robot_connectivity import (
    RecordOfflineCommand,
    RecordOnlineCommand,
    RobotConnectivityUseCase,
)

logger = structlog.get_logger(__name__)


_LIVELINESS_KE = "leitstand/robot/*/online"
_LIVELINESS_PREFIX = "leitstand/robot/"
_LIVELINESS_SUFFIX = "/online"
_METADATA_TEMPLATE = "leitstand/robot/{robot_id}/metadata"
_METADATA_QUERY_TIMEOUT_S = 3.0
_WORKER_JOIN_TIMEOUT_S = 2.0
_METADATA_FETCH_WORKERS = 8
# Robot id must be safe to splice into a Zenoh key segment:
# no path separators, wildcards, admin-namespace markers, or whitespace.
_ROBOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ZenohRobotConnectivityAdapter:
    """Liveliness subscriber + metadata querier."""

    def __init__(
        self,
        session: zenoh.Session,
        use_case: RobotConnectivityUseCase,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._session = session
        self._use_case = use_case
        self._loop = loop
        self._subscriber: Any = None
        self._worker: threading.Thread | None = None
        # Metadata fetches run in parallel so one slow responder
        # does not stall the rest of the fleet's discovery during
        # history replay.
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._closed = threading.Event()

    def start(self) -> None:
        if self._subscriber is not None:
            return
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_METADATA_FETCH_WORKERS,
            thread_name_prefix="leitstand-meta",
        )
        self._subscriber = self._session.liveliness().declare_subscriber(
            _LIVELINESS_KE, history=True
        )
        logger.info("liveliness_subscriber_declared", key=_LIVELINESS_KE)
        self._worker = threading.Thread(
            target=self._consume,
            name="leitstand-liveliness",
            daemon=True,
        )
        self._worker.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        sub, worker, executor = self._subscriber, self._worker, self._executor
        self._subscriber = None
        self._worker = None
        self._executor = None
        if sub is not None:
            try:
                sub.undeclare()
            except Exception as e:  # noqa: BLE001 - best-effort teardown
                logger.warning("subscriber_undeclare_failed", error=str(e))
        if worker is not None and worker.is_alive():
            worker.join(timeout=_WORKER_JOIN_TIMEOUT_S)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _consume(self) -> None:
        sub = self._subscriber
        if sub is None:
            return
        try:
            for sample in sub:
                self._handle_sample(sample)
        except Exception as e:  # noqa: BLE001 - subscriber teardown can race
            if not self._closed.is_set():
                logger.exception("liveliness_consumer_error", error=str(e))

    def _handle_sample(self, sample: Any) -> None:
        try:
            key = str(sample.key_expr)
            robot_id = _extract_robot_id(key)
            if robot_id is None:
                logger.debug("liveliness_key_shape_mismatch", key=key)
                return
            if sample.kind == SampleKind.PUT:
                executor = self._executor
                if executor is None or self._closed.is_set():
                    self._handle_put(robot_id)
                else:
                    try:
                        executor.submit(self._handle_put, robot_id)
                    except RuntimeError:
                        # Executor already shutting down; run inline.
                        self._handle_put(robot_id)
            elif sample.kind == SampleKind.DELETE:
                command = RecordOfflineCommand(robot_id=robot_id)
                fut = asyncio.run_coroutine_threadsafe(
                    self._use_case.record_offline(command), self._loop
                )
                fut.add_done_callback(_log_future_failure("record_offline", robot_id))
            else:
                logger.debug(
                    "unexpected_sample_kind",
                    kind=repr(sample.kind),
                    key=key,
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("sample_handler_error", error=str(e))

    def _handle_put(self, robot_id: str) -> None:
        if not _ROBOT_ID_RE.fullmatch(robot_id):
            logger.warning("invalid_robot_id_rejected", robot_id=robot_id)
            return
        fetched = self._fetch_metadata(robot_id)
        if fetched is None:
            return
        metadata, active_run_id, claim_reported = fetched
        command = RecordOnlineCommand(
            robot_id=robot_id,
            metadata=metadata,
            active_run_id=active_run_id,
            claim_reported=claim_reported,
        )
        fut = asyncio.run_coroutine_threadsafe(self._use_case.record_online(command), self._loop)
        fut.add_done_callback(_log_future_failure("record_online", robot_id))

    def _fetch_metadata(self, robot_id: str) -> tuple[Metadata, str | None, bool] | None:
        """Return the metadata, the run the robot claims (or None), and whether it said."""
        key = _METADATA_TEMPLATE.format(robot_id=robot_id)
        try:
            replies = self._session.get(key, timeout=_METADATA_QUERY_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            logger.warning("metadata_query_failed", robot_id=robot_id, error=str(e))
            return None
        for reply in replies:
            payload = _reply_payload_bytes(reply)
            if payload is None:
                continue
            try:
                data = json.loads(payload.decode("utf-8"))
                claim_reported = isinstance(data, dict) and "active_run_id" in data
                active_run_id = data.pop("active_run_id", None) if claim_reported else None
                if active_run_id is not None and not isinstance(active_run_id, str):
                    raise ValueError("active_run_id must be a string or null")
                metadata = Metadata.model_validate(data)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "invalid_metadata_payload",
                    robot_id=robot_id,
                    error=str(e),
                )
                continue
            if metadata.id != robot_id:
                logger.warning(
                    "metadata_id_mismatch",
                    liveliness_id=robot_id,
                    payload_id=metadata.id,
                )
                continue
            return metadata, active_run_id, claim_reported
        logger.warning(
            "metadata_query_no_reply",
            robot_id=robot_id,
            timeout_s=_METADATA_QUERY_TIMEOUT_S,
        )
        return None


def _extract_robot_id(key: str) -> str | None:
    """Extract `<id>` from a key shaped like `leitstand/robot/<id>/online`."""
    if not key.startswith(_LIVELINESS_PREFIX) or not key.endswith(_LIVELINESS_SUFFIX):
        return None
    inner = key[len(_LIVELINESS_PREFIX) : -len(_LIVELINESS_SUFFIX)]
    if not inner or "/" in inner:
        return None
    return inner


def _reply_payload_bytes(reply: Any) -> bytes | None:
    """Pull the bytes payload out of a Zenoh reply, or None on error."""
    err = getattr(reply, "err", None)
    if err is not None:
        logger.warning("metadata_reply_error", error=str(err))
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
            logger.exception("use_case_failed", action=action, robot_id=robot_id)

    return _cb
