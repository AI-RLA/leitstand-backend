"""Unit tests for ZenohMissionDispatcherAdapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from google.protobuf import json_format
from leitstand.robot.v1 import mission_pb2

from leitstand_backend.adapters.outbound.messaging.zenoh.mission.mission_dispatcher_adapter import (
    ZenohMissionDispatcherAdapter,
)
from leitstand_backend.domain.errors import MissionDispatchTimeout, MissionRejectedByRobot
from leitstand_backend.domain.model.mission.mission import Mission, NavigationStage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"


def _mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="m1",
        stages=[
            NavigationStage(
                stage_id=uuid4(),
                waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
            )
        ],
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )


def _reply_with_payload(payload_bytes: bytes) -> MagicMock:
    """Build a Zenoh reply mock whose .ok.payload.to_bytes() returns ``payload_bytes``."""
    payload = MagicMock()
    payload.to_bytes.return_value = payload_bytes
    ok = MagicMock(payload=payload)
    reply = MagicMock(err=None, ok=ok, payload=payload)
    return reply


def _reply_with_error(message: str = "boom") -> MagicMock:
    return MagicMock(err=message)


def _response_json(accepted: bool, reason: str | None = None) -> bytes:
    resp = mission_pb2.MissionDispatchResponse(accepted=accepted)
    if reason is not None:
        resp.reason = reason
    return json_format.MessageToJson(resp, preserving_proto_field_name=True).encode("utf-8")


def _accept_reply() -> MagicMock:
    return _reply_with_payload(_response_json(True))


def _reject_reply(reason: str) -> MagicMock:
    return _reply_with_payload(_response_json(False, reason))


def _malformed_reply() -> MagicMock:
    return _reply_with_payload(b"{not json")


def _session_returning(replies: list[MagicMock]) -> MagicMock:
    session = MagicMock()
    session.get.return_value = iter(replies)
    return session


# ---------------------------------------------------------------------------
# dispatch()


@pytest.mark.asyncio
async def test_dispatch_happy_path_records_get_call():
    session = _session_returning([_accept_reply()])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission = _mission()

    await adapter.dispatch(mission, ROBOT_ID)

    session.get.assert_called_once()
    call = session.get.call_args
    assert call.args[0] == f"leitstand/robot/{ROBOT_ID}/mission/_action/send_goal"
    assert b'"mission_id"' in call.kwargs["payload"]
    assert call.kwargs["timeout"] > 0


@pytest.mark.asyncio
async def test_dispatch_empty_replies_raises_timeout():
    session = _session_returning([])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission = _mission()

    with pytest.raises(MissionDispatchTimeout) as excinfo:
        await adapter.dispatch(mission, ROBOT_ID)
    assert excinfo.value.mission_id == mission.mission_id


@pytest.mark.asyncio
async def test_dispatch_robot_reject_raises_with_reason():
    session = _session_returning([_reject_reply("unknown site")])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission = _mission()

    with pytest.raises(MissionRejectedByRobot) as excinfo:
        await adapter.dispatch(mission, ROBOT_ID)
    assert excinfo.value.reason == "unknown site"


@pytest.mark.asyncio
async def test_dispatch_malformed_reply_then_accept_succeeds():
    session = _session_returning([_malformed_reply(), _accept_reply()])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission = _mission()

    await adapter.dispatch(mission, ROBOT_ID)
    session.get.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_all_replies_malformed_raises_timeout():
    session = _session_returning([_malformed_reply(), _malformed_reply()])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission = _mission()

    with pytest.raises(MissionDispatchTimeout):
        await adapter.dispatch(mission, ROBOT_ID)


@pytest.mark.asyncio
async def test_dispatch_skips_error_reply_and_accepts_next():
    session = _session_returning([_reply_with_error(), _accept_reply()])
    adapter = ZenohMissionDispatcherAdapter(session=session)

    await adapter.dispatch(_mission(), ROBOT_ID)


# ---------------------------------------------------------------------------
# cancel() / pause() / resume()


@pytest.mark.asyncio
async def test_cancel_sends_payload_on_cancel_key():
    session = _session_returning([])
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission_id = uuid4()

    await adapter.cancel(mission_id, ROBOT_ID, mode=CancelMode.IMMEDIATE)

    session.get.assert_called_once()
    call = session.get.call_args
    assert call.args[0] == f"leitstand/robot/{ROBOT_ID}/mission/_action/cancel_goal"
    assert b'"CANCEL_MODE_IMMEDIATE"' in call.kwargs["payload"]


@pytest.mark.asyncio
async def test_pause_puts_to_pause_key_empty_payload():
    session = MagicMock()
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission_id = uuid4()

    await adapter.pause(mission_id, ROBOT_ID)

    session.put.assert_called_once_with(
        f"leitstand/robot/{ROBOT_ID}/instant/pause",
        b"",
    )


@pytest.mark.asyncio
async def test_resume_puts_to_resume_key_empty_payload():
    session = MagicMock()
    adapter = ZenohMissionDispatcherAdapter(session=session)
    mission_id = uuid4()

    await adapter.resume(mission_id, ROBOT_ID)

    session.put.assert_called_once_with(
        f"leitstand/robot/{ROBOT_ID}/instant/resume",
        b"",
    )
