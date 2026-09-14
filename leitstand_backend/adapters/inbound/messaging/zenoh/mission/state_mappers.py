"""Named-field mapper from the proto mission-state frame to the in-house model.

The anti-corruption layer for the state direction: every field is mapped
explicitly, enum values are translated through closed dictionaries, and the
in-house pydantic model re-validates bounds the wire cannot enforce (the
proto-JSON parser admits values like NaN that pydantic must reject).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from google.protobuf import timestamp_pb2
from leitstand.robot.v1 import mission_state_pb2

from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorReference,
    ErrorSeverity,
    MissionError,
    MissionExecStatus,
    MissionStateMessage,
    StageState,
)
from leitstand_backend.domain.model.mission.stage_status import StageStatus

_MISSION_EXEC_STATUS_FROM_PROTO: dict[int, MissionExecStatus] = {
    mission_state_pb2.MISSION_EXEC_STATUS_RUNNING: MissionExecStatus.RUNNING,
    mission_state_pb2.MISSION_EXEC_STATUS_PAUSED: MissionExecStatus.PAUSED,
    mission_state_pb2.MISSION_EXEC_STATUS_SUCCEEDED: MissionExecStatus.SUCCEEDED,
    mission_state_pb2.MISSION_EXEC_STATUS_FAILED: MissionExecStatus.FAILED,
    mission_state_pb2.MISSION_EXEC_STATUS_CANCELLED: MissionExecStatus.CANCELLED,
}

_STAGE_STATUS_FROM_PROTO: dict[int, StageStatus] = {
    mission_state_pb2.STAGE_STATUS_WAITING: StageStatus.WAITING,
    mission_state_pb2.STAGE_STATUS_INITIALIZING: StageStatus.INITIALIZING,
    mission_state_pb2.STAGE_STATUS_RUNNING: StageStatus.RUNNING,
    mission_state_pb2.STAGE_STATUS_PAUSED: StageStatus.PAUSED,
    mission_state_pb2.STAGE_STATUS_FINISHED: StageStatus.FINISHED,
    mission_state_pb2.STAGE_STATUS_FAILED: StageStatus.FAILED,
    mission_state_pb2.STAGE_STATUS_CANCELLED: StageStatus.CANCELLED,
    mission_state_pb2.STAGE_STATUS_SKIPPED: StageStatus.SKIPPED,
}

_SEVERITY_FROM_PROTO: dict[int, ErrorSeverity] = {
    mission_state_pb2.ERROR_SEVERITY_WARNING: ErrorSeverity.WARNING,
    mission_state_pb2.ERROR_SEVERITY_FATAL: ErrorSeverity.FATAL,
}


def _utc(ts: timestamp_pb2.Timestamp) -> datetime:
    return ts.ToDatetime(tzinfo=timezone.utc)


def _stage_state_from_proto(state: mission_state_pb2.StageState) -> StageState:
    status = _STAGE_STATUS_FROM_PROTO.get(state.status)
    if status is None:
        raise ValueError(f"unmapped stage status {state.status} for stage {state.stage_id}")
    return StageState(
        stage_id=UUID(state.stage_id),
        status=status,
        started_at=_utc(state.started_at) if state.HasField("started_at") else None,
        ended_at=_utc(state.ended_at) if state.HasField("ended_at") else None,
        progress=state.progress,
        result=dict(state.result) or None,
    )


def _error_from_proto(error: mission_state_pb2.Error) -> MissionError:
    # Unknown / unspecified severities degrade to FATAL per the contract.
    severity = _SEVERITY_FROM_PROTO.get(error.severity, ErrorSeverity.FATAL)
    # The wire carries no origin; a parsed error is by definition robot-reported.
    return MissionError(
        origin=ErrorOrigin.ROBOT,
        severity=severity,
        type=error.type,
        references=[ErrorReference(key=r.key, value=r.value) for r in error.references],
        description=error.description,
    )


def mission_state_from_proto(state: mission_state_pb2.MissionState) -> MissionStateMessage:
    """Rebuild the in-house mission-state message from a proto frame.

    Raises ``ValueError`` for frames the backend must not interpret (missing timestamp,
    unmapped execution status, duplicate stage id); the calling adapter drops and logs
    them. Pydantic re-validates bounds on construction.
    """
    if not state.HasField("timestamp"):
        raise ValueError(f"mission state frame for run {state.run_id} is missing its timestamp")

    exec_status = _MISSION_EXEC_STATUS_FROM_PROTO.get(state.exec_status)
    if exec_status is None:
        raise ValueError(f"unmapped exec status {state.exec_status} for run {state.run_id}")

    stage_states = [_stage_state_from_proto(s) for s in state.stage_states]
    if len({s.stage_id for s in stage_states}) != len(stage_states):
        raise ValueError(f"duplicate stage id in mission state frame for run {state.run_id}")

    return MissionStateMessage(
        run_id=UUID(state.run_id),
        header_id=state.header_id,
        timestamp=_utc(state.timestamp),
        exec_status=exec_status,
        current_stage_index=state.current_stage_index,
        stage_states=stage_states,
        errors=[_error_from_proto(e) for e in state.errors],
    )
