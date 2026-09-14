"""Unit tests for MissionStateService using in-memory fakes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from leitstand_backend.application.mission_state_service import MissionStateService
from leitstand_backend.domain.model.mission.mission_run import MissionRun
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorOrigin,
    ErrorSeverity,
    MissionError,
    MissionExecStatus,
    MissionStateMessage,
    StageState,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stage_status import StageStatus
from leitstand_backend.ports.inbound.mission_state import (
    HandleRobotOfflineCommand,
    RecordMissionStateCommand,
)
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.runs import mission_run

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_ID = "scout-mini-04"


def _run(status: RunStatus, robot_id: str = ROBOT_ID) -> MissionRun:
    return mission_run(robot_id=robot_id, status=status, when=UTC_NOW)


def _state_for(
    run_id: UUID,
    *,
    exec_status: MissionExecStatus = MissionExecStatus.RUNNING,
    header_id: int = 1,
    stage_states: list[StageState] | None = None,
) -> MissionStateMessage:
    return MissionStateMessage(
        run_id=run_id,
        header_id=header_id,
        timestamp=UTC_NOW,
        exec_status=exec_status,
        current_stage_index=0,
        stage_states=stage_states or [],
    )


def _make_svc():
    runs = InMemoryMissionRunRepository()
    events = InMemoryEventPublisher()
    return MissionStateService(runs=runs, events=events), runs, events


async def _record(svc, run: MissionRun, exec_status: MissionExecStatus, header_id: int = 1):
    await svc.record(
        RecordMissionStateCommand(
            robot_id=run.robot_id,
            state=_state_for(run.run_id, exec_status=exec_status, header_id=header_id),
        )
    )


@pytest.mark.asyncio
async def test_record_acks_dispatched_run_to_running():
    svc, runs, events = _make_svc()
    run = await runs.create(_run(RunStatus.DISPATCHED))

    await _record(svc, run, MissionExecStatus.RUNNING)

    assert (await runs.get(run.run_id)).status is RunStatus.RUNNING
    assert (await runs.get(run.run_id)).started_at is not None
    lifecycle = [(t, p) for t, p, _ in events.published if t.endswith("/lifecycle")]
    assert lifecycle == [
        (
            f"events/mission/{run.mission_id}/lifecycle",
            {
                "mission_id": str(run.mission_id),
                "run_id": str(run.run_id),
                "robot_id": ROBOT_ID,
                "status": "RUNNING",
                "trigger": "ack",
            },
        )
    ]


@pytest.mark.asyncio
async def test_record_moves_a_pending_run_on_from_the_robots_first_frame():
    """The executor starts before it answers the dispatch query; its first frame counts."""
    svc, runs, _ = _make_svc()
    run = await runs.create(_run(RunStatus.PENDING))
    await _record(svc, run, MissionExecStatus.RUNNING)
    assert (await runs.get(run.run_id)).status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_record_completes_dispatched_run_from_latched_terminal():
    svc, runs, _ = _make_svc()
    run = await runs.create(_run(RunStatus.DISPATCHED))
    await _record(svc, run, MissionExecStatus.SUCCEEDED)
    assert (await runs.get(run.run_id)).status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_record_pauses_and_resumes_via_exec_status():
    svc, runs, _ = _make_svc()
    run = await runs.create(_run(RunStatus.RUNNING))
    await _record(svc, run, MissionExecStatus.PAUSED, header_id=2)
    assert (await runs.get(run.run_id)).status is RunStatus.PAUSED
    await _record(svc, run, MissionExecStatus.RUNNING, header_id=3)
    assert (await runs.get(run.run_id)).status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_record_completes_running_run_and_republishes_resolved_state():
    svc, runs, events = _make_svc()
    run = await runs.create(_run(RunStatus.RUNNING))

    await _record(svc, run, MissionExecStatus.SUCCEEDED)

    assert (await runs.get(run.run_id)).status is RunStatus.SUCCEEDED
    assert (await runs.get(run.run_id)).ended_at is not None
    state_payloads = [p for t, p, latch in events.published if t.endswith("/state") and latch]
    assert state_payloads[-1]["run_id"] == str(run.run_id)
    assert state_payloads[-1]["stage_states"][0]["status"] == "FINISHED"


@pytest.mark.asyncio
async def test_record_drops_a_frame_for_a_settled_run():
    svc, runs, events = _make_svc()
    run = await runs.create(_run(RunStatus.SUCCEEDED))
    sid = run.stages[0].stage_id
    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                run.run_id,
                exec_status=MissionExecStatus.RUNNING,
                stage_states=[StageState(stage_id=sid, status=StageStatus.RUNNING)],
            ),
        )
    )
    assert (await runs.get(run.run_id)).status is RunStatus.SUCCEEDED
    assert [t for t, _, _ in events.published if t.endswith("/lifecycle")] == []
    assert await runs.get_stage_runs(run.run_id) == []


@pytest.mark.asyncio
async def test_record_drops_a_frame_from_a_robot_that_is_not_the_runs():
    svc, runs, events = _make_svc()
    run = await runs.create(_run(RunStatus.DISPATCHED, robot_id="bonirob-2"))
    await svc.record(
        RecordMissionStateCommand(
            robot_id="zombie", state=_state_for(run.run_id, exec_status=MissionExecStatus.SUCCEEDED)
        )
    )
    assert (await runs.get(run.run_id)).status is RunStatus.DISPATCHED
    assert events.published == []


@pytest.mark.asyncio
async def test_record_drops_an_unknown_run():
    svc, _, events = _make_svc()
    await svc.record(RecordMissionStateCommand(robot_id=ROBOT_ID, state=_state_for(uuid4())))
    assert events.published == []


@pytest.mark.asyncio
async def test_stage_rows_are_keyed_by_run_and_ordered_by_header():
    svc, runs, _ = _make_svc()
    run = await runs.create(_run(RunStatus.RUNNING))
    sid = run.stages[0].stage_id

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                run.run_id,
                header_id=5,
                stage_states=[StageState(stage_id=sid, status=StageStatus.RUNNING, progress=0.5)],
            ),
        )
    )
    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                run.run_id,
                header_id=4,
                stage_states=[StageState(stage_id=sid, status=StageStatus.WAITING, progress=0.0)],
            ),
        )
    )
    (row,) = await runs.get_stage_runs(run.run_id)
    assert row.status is StageStatus.RUNNING and row.progress == 0.5
    assert (await runs.get(run.run_id)).last_report.header_id == 4


@pytest.mark.asyncio
async def test_record_failure_persists_robot_errors_as_durable_cause():
    svc, runs, _ = _make_svc()
    run = await runs.create(_run(RunStatus.RUNNING))
    state = _state_for(run.run_id, exec_status=MissionExecStatus.FAILED)
    state.errors = [
        MissionError(
            origin=ErrorOrigin.ROBOT,
            severity=ErrorSeverity.FATAL,
            type="nav2_unavailable",
            description="nav2 did not appear",
        )
    ]
    await svc.record(RecordMissionStateCommand(robot_id=ROBOT_ID, state=state))
    updated = await runs.get(run.run_id)
    assert updated.status is RunStatus.FAILED
    assert updated.failure_errors == state.errors


@pytest.mark.asyncio
async def test_handle_robot_offline_leaves_every_run_as_it_found_it():
    # Losing contact is not evidence the machine stopped, so nothing is settled and no robot is
    # released; the run's own frames decide it when contact returns.
    svc, runs, events = _make_svc()
    executing = await runs.create(_run(RunStatus.RUNNING, robot_id="r1"))
    pending = await runs.create(_run(RunStatus.PENDING, robot_id="r2"))
    done = await runs.create(_run(RunStatus.SUCCEEDED, robot_id="r3"))
    for r in ("r1", "r2", "r3"):
        await svc.handle_robot_offline(HandleRobotOfflineCommand(robot_id=r))

    assert (await runs.get(executing.run_id)).status is RunStatus.RUNNING
    assert (await runs.get(pending.run_id)).status is RunStatus.PENDING
    assert (await runs.get(done.run_id)).status is RunStatus.SUCCEEDED
    assert (await runs.get(executing.run_id)).failure_errors is None
    assert [p for t, p, _ in events.published if t.endswith("/lifecycle")] == []


@pytest.mark.asyncio
async def test_a_cleanup_stage_the_robot_reports_is_kept_under_its_parent():
    """A cleanup stage sorts with the stage whose cancel started it, and survives the closing."""
    from leitstand_backend.domain.model.mission.mission import NavigationStage
    from leitstand_backend.domain.model.mission.waypoint import WGS84Waypoint

    svc, runs, _events = _make_svc()
    cleanup = NavigationStage(stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)])
    main = NavigationStage(
        stage_id=uuid4(), waypoints=[WGS84Waypoint(lat=52.3, lon=8.05)], on_cancel=[cleanup]
    )
    run = await runs.create(_run(RunStatus.RUNNING).model_copy(update={"stages": [main]}))

    await svc.record(
        RecordMissionStateCommand(
            robot_id=ROBOT_ID,
            state=_state_for(
                run.run_id,
                exec_status=MissionExecStatus.CANCELLED,
                header_id=3,
                stage_states=[
                    StageState(stage_id=main.stage_id, status=StageStatus.CANCELLED),
                    StageState(stage_id=cleanup.stage_id, status=StageStatus.FINISHED),
                ],
            ),
        )
    )

    rows = {r.stage_id: r for r in await runs.get_stage_runs(run.run_id)}
    assert rows[main.stage_id].status is StageStatus.CANCELLED
    assert rows[main.stage_id].status_source == "robot"
    child = rows[cleanup.stage_id]
    assert (child.stage_index, child.parent_stage_id, child.status) == (
        0,
        main.stage_id,
        StageStatus.FINISHED,
    )
    assert (await runs.get(run.run_id)).status is RunStatus.CANCELLED
