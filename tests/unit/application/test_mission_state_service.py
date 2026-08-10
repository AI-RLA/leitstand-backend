"""Unit tests for MissionStateService using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.application.mission_state_service import MissionStateService
from leitstand_backend.domain.model.mission.mission import Mission, MissionStatus, NavigationStage
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
    MissionExecStatus,
    MissionStateMessage,
)
from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint
from leitstand_backend.ports.inbound.mission_state import (
    HandleRobotOfflineCommand,
    RecordMissionStateCommand,
)
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"


def _build_mission() -> Mission:
    return Mission(
        mission_id=uuid4(),
        name="test mission",
        stages=[
            NavigationStage(
                stage_id=uuid4(),
                waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)],
            )
        ],
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )


def _state_for(
    mission_id: UUID,
    *,
    exec_status: MissionExecStatus = MissionExecStatus.RUNNING,
    header_id: int = 1,
) -> MissionStateMessage:
    return MissionStateMessage(
        mission_id=mission_id,
        header_id=header_id,
        timestamp=UTC_NOW,
        exec_status=exec_status,
        current_stage_index=0,
    )


def _make_svc():
    repo = InMemoryMissionRepository()
    events = InMemoryEventPublisher()
    svc = MissionStateService(repo=repo, events=events)
    return svc, repo, events


@pytest.mark.asyncio
async def test_record_acks_dispatched_mission_to_running():
    svc, repo, events = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.DISPATCHED)

    state = _state_for(mission.mission_id, exec_status=MissionExecStatus.RUNNING)
    await svc.record(RecordMissionStateCommand(robot_id=ROBOT_ID, state=state))

    # First RUNNING telemetry after dispatch drives DISPATCHED --ack--> RUNNING.
    assert await repo.get_status(mission.mission_id) is MissionStatus.RUNNING
    lifecycle = [t for t, _, _ in events.published if t.endswith("/lifecycle")]
    assert lifecycle == [f"events/mission/{mission.mission_id}/lifecycle"]


@pytest.mark.asyncio
async def test_record_completes_dispatched_mission_from_latched_terminal():
    """A latched terminal SUCCEEDED must complete a mission still at DISPATCHED
    (its RUNNING/ack frame was lost), not strand it."""
    svc, repo, _ = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.DISPATCHED)

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(mission.mission_id, exec_status=MissionExecStatus.SUCCEEDED),
        )
    )

    assert await repo.get_status(mission.mission_id) is MissionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_record_pauses_and_resumes_via_exec_status():
    svc, repo, _ = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(mission.mission_id, exec_status=MissionExecStatus.PAUSED, header_id=2),
        )
    )
    assert await repo.get_status(mission.mission_id) is MissionStatus.PAUSED

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                mission.mission_id, exec_status=MissionExecStatus.RUNNING, header_id=3
            ),
        )
    )
    assert await repo.get_status(mission.mission_id) is MissionStatus.RUNNING


@pytest.mark.asyncio
async def test_record_completes_running_mission_and_emits_lifecycle():
    svc, repo, events = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                mission.mission_id,
                exec_status=MissionExecStatus.SUCCEEDED,
            ),
        )
    )

    assert await repo.get_status(mission.mission_id) is MissionStatus.SUCCEEDED
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert lifecycle == [
        (
            f"events/mission/{mission.mission_id}/lifecycle",
            {
                "mission_id": str(mission.mission_id),
                "status": "SUCCEEDED",
                "trigger": "complete",
            },
        )
    ]
    # On completion the resolved per-stage view is republished (latched): the mission's
    # stage resolves to FINISHED.
    state_payloads = [p for t, p, latch in events.published if t.endswith("/state") and latch]
    assert state_payloads
    assert state_payloads[-1]["stage_states"][0]["status"] == "FINISHED"


@pytest.mark.asyncio
async def test_record_drops_telemetry_that_does_not_warrant_a_transition():
    """A DRAFT mission must not be driven terminal by a stray robot frame."""
    svc, repo, events = _make_svc()
    mission = await repo.save(_build_mission())  # DRAFT

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                mission.mission_id,
                exec_status=MissionExecStatus.SUCCEEDED,
            ),
        )
    )

    # No legal transition from DRAFT for this telemetry: status unchanged, no lifecycle event,
    # and the non-executing gate drops the live stage write.
    assert await repo.get_status(mission.mission_id) is MissionStatus.DRAFT
    assert [t for t, _, _ in events.published if t.endswith("/lifecycle")] == []
    assert await repo.get_stage_states(mission.mission_id) == []


@pytest.mark.asyncio
async def test_handle_robot_offline_fails_active_missions():
    svc, repo, events = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)
    await repo.assign_robot(mission.mission_id, ROBOT_ID, UTC_NOW)

    await svc.handle_robot_offline(HandleRobotOfflineCommand(robot_id=ROBOT_ID))

    assert await repo.get_status(mission.mission_id) is MissionStatus.FAILED
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert lifecycle == [
        (
            f"events/mission/{mission.mission_id}/lifecycle",
            {
                "mission_id": str(mission.mission_id),
                "status": "FAILED",
                "trigger": "fail",
                "reason": "robot offline mid-mission",
            },
        )
    ]


@pytest.mark.asyncio
async def test_handle_robot_offline_skips_terminal_missions():
    svc, repo, _ = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.SUCCEEDED)
    await repo.assign_robot(mission.mission_id, ROBOT_ID, UTC_NOW)

    await svc.handle_robot_offline(HandleRobotOfflineCommand(robot_id=ROBOT_ID))

    assert await repo.get_status(mission.mission_id) is MissionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_handle_robot_offline_leaves_assigned_mission_untouched():
    """An assigned-but-not-dispatched mission has no goal on the robot; a robot going
    offline must not fail it (FAIL is not a legal transition from ASSIGNED)."""
    svc, repo, events = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.ASSIGNED)
    await repo.assign_robot(mission.mission_id, ROBOT_ID, UTC_NOW)

    await svc.handle_robot_offline(HandleRobotOfflineCommand(robot_id=ROBOT_ID))

    assert await repo.get_status(mission.mission_id) is MissionStatus.ASSIGNED
    assert [t for t, _, _ in events.published if t.endswith("/lifecycle")] == []


@pytest.mark.asyncio
async def test_record_failure_persists_robot_errors_as_durable_cause():
    svc, repo, _ = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)

    state = _state_for(mission.mission_id, exec_status=MissionExecStatus.FAILED)
    state.errors = [
        MissionError(
            origin=ErrorOrigin.ROBOT,
            severity=ErrorSeverity.FATAL,
            type="nav2_unavailable",
            description="nav2 did not appear",
        )
    ]
    await svc.record(RecordMissionStateCommand(robot_id=ROBOT_ID, state=state))

    assert await repo.get_status(mission.mission_id) is MissionStatus.FAILED
    record = await repo.get_record(mission.mission_id)
    # The robot's structured errors become the durable failure cause, byte-for-byte.
    assert record is not None
    assert record.failure_errors == state.errors


@pytest.mark.asyncio
async def test_handle_robot_offline_records_backend_failure_cause():
    svc, repo, _ = _make_svc()
    mission = await repo.save(_build_mission())
    repo.set_status_directly(mission.mission_id, MissionStatus.RUNNING)
    await repo.assign_robot(mission.mission_id, ROBOT_ID, UTC_NOW)

    await svc.handle_robot_offline(HandleRobotOfflineCommand(robot_id=ROBOT_ID))

    record = await repo.get_record(mission.mission_id)
    assert record is not None and record.failure_errors is not None
    (error,) = record.failure_errors
    assert error.origin is ErrorOrigin.BACKEND
    assert error.type == "robot_offline_mid_mission"
