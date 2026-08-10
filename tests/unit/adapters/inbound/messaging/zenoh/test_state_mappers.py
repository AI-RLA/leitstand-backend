"""Unit tests for the state-direction ACL mapper.

Covers the enum translation tables (exhaustively pinned against the proto
descriptors), optional-field presence, the strict rejections the adapter
relies on, and the NaN case the proto-JSON parser admits but pydantic must
reject.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from google.protobuf import json_format, timestamp_pb2
from leitstand.robot.v1 import mission_state_pb2
from pydantic import ValidationError

from leitstand_backend.adapters.inbound.messaging.zenoh.mission.state_mappers import (
    _MISSION_EXEC_STATUS_FROM_PROTO,
    _SEVERITY_FROM_PROTO,
    _STAGE_STATUS_FROM_PROTO,
    mission_state_from_proto,
)
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorSeverity,
    MissionExecStatus,
    StageStatus,
)

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(moment: datetime) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(moment)
    return ts


def _frame(**overrides) -> mission_state_pb2.MissionState:
    frame = mission_state_pb2.MissionState(
        mission_id=str(uuid4()),
        header_id=7,
        timestamp=_ts(_T0),
        exec_status=mission_state_pb2.MISSION_EXEC_STATUS_RUNNING,
        current_stage_index=1,
    )
    for name, value in overrides.items():
        setattr(frame, name, value)
    return frame


def test_full_frame_maps_with_utc_timestamps_and_result() -> None:
    stage_id = uuid4()
    frame = _frame()
    frame.stage_states.append(
        mission_state_pb2.StageState(
            stage_id=str(stage_id),
            status=mission_state_pb2.STAGE_STATUS_RUNNING,
            started_at=_ts(_T0),
            progress=0.5,
            result={"distance_m": "12.5"},
        )
    )
    frame.errors.append(
        mission_state_pb2.Error(
            severity=mission_state_pb2.ERROR_SEVERITY_WARNING,
            type="nav2_send_goal_timeout",
            description="send_goal timed out",
        )
    )

    state = mission_state_from_proto(frame)

    assert state.header_id == 7
    assert state.timestamp == _T0 and state.timestamp.tzinfo is timezone.utc
    assert state.exec_status is MissionExecStatus.RUNNING
    stage = state.stage_states[0]
    assert stage.stage_id == stage_id
    assert stage.started_at == _T0 and stage.ended_at is None
    assert stage.result == {"distance_m": "12.5"}
    assert state.errors[0].severity is ErrorSeverity.WARNING
    assert state.errors[0].type == "nav2_send_goal_timeout"


def test_terminal_status_maps() -> None:
    state = mission_state_from_proto(
        _frame(exec_status=mission_state_pb2.MISSION_EXEC_STATUS_SUCCEEDED)
    )
    assert state.exec_status is MissionExecStatus.SUCCEEDED


def test_empty_result_map_becomes_none() -> None:
    frame = _frame()
    frame.stage_states.append(
        mission_state_pb2.StageState(
            stage_id=str(uuid4()), status=mission_state_pb2.STAGE_STATUS_WAITING
        )
    )
    assert mission_state_from_proto(frame).stage_states[0].result is None


def test_unspecified_severity_degrades_to_fatal() -> None:
    frame = _frame()
    frame.errors.append(mission_state_pb2.Error(type="x", description="y"))
    assert mission_state_from_proto(frame).errors[0].severity is ErrorSeverity.FATAL


def test_missing_timestamp_is_rejected() -> None:
    frame = _frame()
    frame.ClearField("timestamp")
    with pytest.raises(ValueError, match="missing its timestamp"):
        mission_state_from_proto(frame)


def test_unspecified_exec_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="unmapped exec status"):
        mission_state_from_proto(
            _frame(exec_status=mission_state_pb2.MISSION_EXEC_STATUS_UNSPECIFIED)
        )


def test_unspecified_stage_status_is_rejected() -> None:
    frame = _frame()
    frame.stage_states.append(mission_state_pb2.StageState(stage_id=str(uuid4())))
    with pytest.raises(ValueError, match="unmapped stage status"):
        mission_state_from_proto(frame)


def test_duplicate_stage_id_is_rejected() -> None:
    stage_id = str(uuid4())
    frame = _frame()
    for _ in range(2):
        frame.stage_states.append(
            mission_state_pb2.StageState(
                stage_id=stage_id, status=mission_state_pb2.STAGE_STATUS_RUNNING
            )
        )
    with pytest.raises(ValueError, match="duplicate stage id"):
        mission_state_from_proto(frame)


def test_nan_progress_from_proto_json_is_rejected_by_domain_validation() -> None:
    # The proto3-JSON spec admits "NaN" as a double; the in-house pydantic
    # bounds are the layer that must refuse it.
    payload = json.dumps(
        {
            "mission_id": str(uuid4()),
            "header_id": 1,
            "timestamp": "2026-01-01T12:00:00Z",
            "exec_status": "MISSION_EXEC_STATUS_RUNNING",
            "current_stage_index": 0,
            "stage_states": [
                {
                    "stage_id": str(uuid4()),
                    "status": "STAGE_STATUS_RUNNING",
                    "progress": "NaN",
                }
            ],
        }
    )
    frame = json_format.Parse(
        payload, mission_state_pb2.MissionState(), ignore_unknown_fields=False
    )
    with pytest.raises(ValidationError):
        mission_state_from_proto(frame)


def test_enum_tables_are_exhaustive_against_the_descriptors() -> None:
    # A new proto enum value must extend the mapper tables and these sets together.
    def non_zero(enum_descriptor) -> set[int]:
        return {v.number for v in enum_descriptor.values if v.number != 0}

    assert set(_MISSION_EXEC_STATUS_FROM_PROTO) == non_zero(
        mission_state_pb2.MissionExecStatus.DESCRIPTOR
    )
    assert set(_STAGE_STATUS_FROM_PROTO) == non_zero(mission_state_pb2.StageStatus.DESCRIPTOR)
    assert set(_SEVERITY_FROM_PROTO) == non_zero(mission_state_pb2.ErrorSeverity.DESCRIPTOR)

    # Reverse: for the wire-only enums the mapper covers the whole type; StageStatus additionally
    # carries the backend-resolved CANCELLED and SKIPPED, so there the mapping is a subset.
    assert set(_MISSION_EXEC_STATUS_FROM_PROTO.values()) == set(MissionExecStatus)
    assert set(_SEVERITY_FROM_PROTO.values()) == set(ErrorSeverity)
    assert set(_STAGE_STATUS_FROM_PROTO.values()) <= set(StageStatus)
