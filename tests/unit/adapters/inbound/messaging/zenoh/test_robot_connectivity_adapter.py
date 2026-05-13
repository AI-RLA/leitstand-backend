"""Unit tests for ZenohRobotConnectivityAdapter with a MagicMock session."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from zenoh import SampleKind

from leitstand_backend.adapters.inbound.messaging.zenoh.robot_connectivity_adapter import (
    ZenohRobotConnectivityAdapter,
    _extract_robot_id,
    _reply_payload_bytes,
)
from leitstand_backend.ports.inbound.robot_connectivity import (
    RecordOfflineCommand,
    RecordOnlineCommand,
)

_RCTS_PATH = (
    "leitstand_backend.adapters.inbound.messaging.zenoh"
    ".robot_connectivity_adapter.asyncio.run_coroutine_threadsafe"
)

# ---------------------------------------------------------------------------
# Helpers — tiny fakes that mirror the shape zenoh-python exposes.
# ---------------------------------------------------------------------------


def _fake_sample(key: str, kind: SampleKind) -> SimpleNamespace:
    return SimpleNamespace(key_expr=key, kind=kind)


class _FakePayload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def to_bytes(self) -> bytes:
        return self._data


def _fake_reply(payload_bytes: bytes | None) -> SimpleNamespace:
    if payload_bytes is None:
        return SimpleNamespace(err="boom", ok=None)
    return SimpleNamespace(err=None, ok=SimpleNamespace(payload=_FakePayload(payload_bytes)))


def _metadata_payload(robot_id: str, **extra) -> bytes:
    return json.dumps({"id": robot_id, **extra}).encode("utf-8")


def _make_tracker(session: MagicMock) -> tuple[ZenohRobotConnectivityAdapter, MagicMock]:
    use_case = MagicMock()
    loop = MagicMock()
    return ZenohRobotConnectivityAdapter(session, use_case, loop), use_case


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_robot_id_matches_expected_shape() -> None:
    assert _extract_robot_id("leitstand/robot/scout_mini_4/online") == "scout_mini_4"
    assert _extract_robot_id("leitstand/robot/r1/online") == "r1"


def test_extract_robot_id_returns_none_for_other_keys() -> None:
    assert _extract_robot_id("leitstand/robot/scout_mini_4/metadata") is None
    assert _extract_robot_id("scout_mini_4/leitstand/online") is None  # old shape
    assert _extract_robot_id("leitstand/robot//online") is None  # empty id
    assert _extract_robot_id("totally_different") is None


def test_reply_payload_bytes_extracts_ok_payload() -> None:
    assert _reply_payload_bytes(_fake_reply(b"hello")) == b"hello"


def test_reply_payload_bytes_skips_errors() -> None:
    assert _reply_payload_bytes(_fake_reply(None)) is None


# ---------------------------------------------------------------------------
# Tracker wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> MagicMock:
    s = MagicMock(name="zenoh.Session")
    sub = MagicMock(name="LivelinessSub")
    sub.__iter__ = lambda self: iter([])
    s.liveliness.return_value.declare_subscriber.return_value = sub
    return s


def test_start_declares_liveliness_subscriber_with_history(session: MagicMock) -> None:
    tracker, _ = _make_tracker(session)
    tracker.start()
    session.liveliness.return_value.declare_subscriber.assert_called_once()
    args, kwargs = session.liveliness.return_value.declare_subscriber.call_args
    assert args[0] == "leitstand/robot/*/online"
    assert kwargs.get("history") is True
    tracker.close()


def test_start_is_idempotent(session: MagicMock) -> None:
    tracker, _ = _make_tracker(session)
    tracker.start()
    tracker.start()
    session.liveliness.return_value.declare_subscriber.assert_called_once()
    tracker.close()


def test_close_undeclares_subscriber(session: MagicMock) -> None:
    tracker, _ = _make_tracker(session)
    tracker.start()
    sub = session.liveliness.return_value.declare_subscriber.return_value
    tracker.close()
    sub.undeclare.assert_called_once()


def test_close_is_idempotent_and_tolerates_undeclare_errors(session: MagicMock) -> None:
    tracker, _ = _make_tracker(session)
    tracker.start()
    sub = session.liveliness.return_value.declare_subscriber.return_value
    sub.undeclare.side_effect = RuntimeError("already gone")
    tracker.close()
    tracker.close()


@patch(_RCTS_PATH)
def test_put_event_queries_metadata_and_invokes_on_online(
    mock_rcts: MagicMock, session: MagicMock
) -> None:
    mock_rcts.return_value = MagicMock()
    session.get.return_value = iter([_fake_reply(_metadata_payload("r1"))])
    tracker, use_case = _make_tracker(session)

    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.PUT))

    session.get.assert_called_once_with("leitstand/robot/r1/metadata", timeout=3.0)
    use_case.record_online.assert_called_once()
    cmd: RecordOnlineCommand = use_case.record_online.call_args[0][0]
    assert cmd.robot_id == "r1"
    assert cmd.metadata.id == "r1"


@patch(_RCTS_PATH)
def test_delete_event_invokes_on_offline(mock_rcts: MagicMock, session: MagicMock) -> None:
    mock_rcts.return_value = MagicMock()
    tracker, use_case = _make_tracker(session)

    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.DELETE))

    use_case.record_offline.assert_called_once()
    cmd: RecordOfflineCommand = use_case.record_offline.call_args[0][0]
    assert cmd.robot_id == "r1"


@pytest.mark.parametrize(
    "robot_id",
    [
        "Bad-ID-Caps",  # uppercase
        "scout.mini",  # dot
        "scout*",  # wildcard
        "scout?",  # wildcard
        "@scout",  # admin-namespace prefix
        "_underscore",  # bad lead char
        "-dash",  # bad lead char
    ],
)
@patch(_RCTS_PATH)
def test_put_with_invalid_robot_id_is_skipped(
    mock_rcts: MagicMock, session: MagicMock, robot_id: str
) -> None:
    tracker, use_case = _make_tracker(session)
    tracker._handle_sample(_fake_sample(f"leitstand/robot/{robot_id}/online", SampleKind.PUT))
    use_case.record_online.assert_not_called()
    session.get.assert_not_called()


@patch(_RCTS_PATH)
def test_put_skips_when_metadata_id_mismatches_key(
    mock_rcts: MagicMock, session: MagicMock
) -> None:
    session.get.return_value = iter([_fake_reply(_metadata_payload("evil_twin"))])
    tracker, use_case = _make_tracker(session)
    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.PUT))
    use_case.record_online.assert_not_called()


@patch(_RCTS_PATH)
def test_put_skips_when_metadata_query_returns_nothing(
    mock_rcts: MagicMock, session: MagicMock
) -> None:
    session.get.return_value = iter([])
    tracker, use_case = _make_tracker(session)
    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.PUT))
    use_case.record_online.assert_not_called()


@patch(_RCTS_PATH)
def test_put_skips_when_metadata_payload_is_garbage(
    mock_rcts: MagicMock, session: MagicMock
) -> None:
    session.get.return_value = iter([_fake_reply(b"\xff\xfe not json")])
    tracker, use_case = _make_tracker(session)
    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.PUT))
    use_case.record_online.assert_not_called()


@patch(_RCTS_PATH)
def test_put_tolerates_session_get_exception(mock_rcts: MagicMock, session: MagicMock) -> None:
    session.get.side_effect = RuntimeError("network down")
    tracker, use_case = _make_tracker(session)
    tracker._handle_sample(_fake_sample("leitstand/robot/r1/online", SampleKind.PUT))
    use_case.record_online.assert_not_called()


@patch(_RCTS_PATH)
def test_handler_does_not_raise_on_unexpected_input(
    mock_rcts: MagicMock, session: MagicMock
) -> None:
    tracker, _ = _make_tracker(session)
    tracker._handle_sample(SimpleNamespace(key_expr="weird/key", kind=None))


@patch(_RCTS_PATH)
def test_consume_drains_subscriber_channel(mock_rcts: MagicMock, session: MagicMock) -> None:
    mock_rcts.return_value = MagicMock()
    session.get.return_value = iter([_fake_reply(_metadata_payload("r1"))])
    sub = session.liveliness.return_value.declare_subscriber.return_value
    sub.__iter__ = lambda self: iter([_fake_sample("leitstand/robot/r1/online", SampleKind.PUT)])

    tracker, use_case = _make_tracker(session)
    tracker.start()
    if tracker._worker is not None:
        tracker._worker.join(timeout=1.0)
    tracker.close()

    use_case.record_online.assert_called_once()
    cmd: RecordOnlineCommand = use_case.record_online.call_args[0][0]
    assert cmd.robot_id == "r1"
