"""RunService: starting, steering and re-running missions, against in-memory fakes.

The two-phase start is driven here the way the orchestrator drives it: ``prepare``, then the
dispatcher, then ``settle``; ``_start`` below is that sequence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from leitstand_backend.application.mission_management_service import MissionManagementService
from leitstand_backend.application.mission_state_service import MissionStateService
from leitstand_backend.application.run_service import RunService
from leitstand_backend.domain.errors import (
    AmbiguousRun,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionArchived,
    MissionDispatchTimeout,
    MissionRejectedByRobot,
    MissionRunInProgress,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RobotOffline,
    RobotOnline,
    RobotRefusedControl,
    RobotUnreachable,
    RunNotFoundError,
    UnsupportedStageKind,
)
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode, ControlRefusal
from leitstand_backend.domain.model.mission.mission_run import MissionRun, RunOrigin
from leitstand_backend.domain.model.mission.mission_state import (
    ErrorSeverity,
    MissionExecStatus,
    MissionStateMessage,
)
from leitstand_backend.domain.model.mission.run_lifecycle import RunTrigger
from leitstand_backend.domain.model.mission.run_status import RunStatus
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, WGS84Waypoint
from leitstand_backend.domain.model.robot.robot import Metadata
from leitstand_backend.domain.model.robot.robot_factsheet import (
    CoverageCapability,
    NavigationCapability,
    PhysicalParameters,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.ports.inbound.mission_management import (
    CreateGeneratedMissionCommand,
    CreateMissionCommand,
    DeleteMissionCommand,
    NavigationStageInput,
    UpdateMissionCommand,
)
from leitstand_backend.ports.inbound.mission_state import RecordMissionStateCommand
from leitstand_backend.ports.inbound.run_management import (
    AnnotateRunCommand,
    CancelRunCommand,
    CloseRunCommand,
    DispatchOutcome,
    PauseRunCommand,
    ResumeRunCommand,
    SettleRunCommand,
    StartRunCommand,
)
from leitstand_backend.ports.outbound.event_publisher import mission_topic
from leitstand_backend.ports.outbound.mission_dispatcher import ControlReply
from tests.fakes.coverage_stage_planning import bind_stage_planning
from tests.fakes.fake_mission_dispatcher import FakeMissionDispatcher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_field_repository import InMemoryFieldRepository
from tests.fakes.in_memory_mission_repository import InMemoryMissionRepository
from tests.fakes.in_memory_mission_run_repository import InMemoryMissionRunRepository
from tests.fakes.in_memory_robot_factsheet_view import InMemoryRobotFactsheetView
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository
from tests.fakes.in_memory_site_repository import InMemorySiteRepository
from tests.fakes.planned_coverage import coverage_stage_for, seeded_field

UTC_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
ROBOT_A = "scout-mini-04"
ROBOT_B = "bonirob-2"
ORIGIN = RunOrigin(kind="manual", actor="human")


def _factsheet(robot_id: str) -> RobotFactsheet:
    return RobotFactsheet(
        robot_id=robot_id,
        navigation=NavigationCapability(
            supported_waypoint_kinds=[WaypointKind.WGS84, WaypointKind.SITE_LOCAL]
        ),
    )


def _stage(lat: float = 52.3) -> NavigationStageInput:
    return NavigationStageInput(waypoints=[WGS84Waypoint(lat=lat, lon=8.05)])


def _site_local_stage(site_id: UUID) -> NavigationStageInput:
    return NavigationStageInput(waypoints=[SiteLocalWaypoint(site_id=site_id, x=1.0, y=2.0)])


class _World:
    """Every collaborator a run needs, wired the way the orchestrator wires them."""

    def __init__(self) -> None:
        self.missions = InMemoryMissionRepository()
        self.runs = InMemoryMissionRunRepository()
        self.dispatcher = FakeMissionDispatcher()
        self.factsheets = InMemoryRobotFactsheetView()
        self.events = InMemoryEventPublisher()
        self.fields = InMemoryFieldRepository()
        self.sites = InMemorySiteRepository()
        self.robots = InMemoryRobotRepository()
        self.audit_calls: list[dict] = []
        self.factsheets.set(_factsheet(ROBOT_A))
        self.factsheets.set(_factsheet(ROBOT_B))

        async def audit(action, target_type, target_id, payload):
            self.audit_calls.append(
                {
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "payload": payload,
                }
            )

        self.run_service = RunService(
            missions=self.missions,
            runs=self.runs,
            dispatcher=self.dispatcher,
            factsheets=self.factsheets,
            fields=self.fields,
            sites=self.sites,
            robots=self.robots,
            events=self.events,
            audit=audit,
        )
        plan, unchanged, _ = bind_stage_planning(self.fields, self.factsheets)
        self.mission_service = MissionManagementService(
            repo=self.missions,
            runs=self.runs,
            factsheets=self.factsheets,
            events=self.events,
            audit=audit,
            sites=self.sites,
            fields=self.fields,
            planner=plan,
            unchanged=unchanged,
        )
        self.state_service = MissionStateService(runs=self.runs, events=self.events)

    async def mission(self, *stages: NavigationStageInput):
        return await self.mission_service.create(
            CreateMissionCommand(name="m", stages=list(stages) or [_stage()])
        )

    async def start(
        self,
        mission_id: UUID,
        robot_id: str | None = ROBOT_A,
        *,
        allow_concurrent: bool = False,
        notes: str | None = None,
    ) -> MissionRun:
        run = await self.run_service.prepare(
            StartRunCommand(
                mission_id=mission_id,
                robot_id=robot_id,
                allow_concurrent=allow_concurrent,
                notes=notes,
                origin=ORIGIN,
            )
        )
        error: Exception | None = None
        try:
            await self.dispatcher.dispatch(run.run_id, run.stages, run.robot_id)
        except MissionRejectedByRobot as exc:
            outcome, reason, error = DispatchOutcome.REJECTED, exc.reason, exc
        except MissionDispatchTimeout as exc:
            outcome, reason, error = DispatchOutcome.TIMEOUT, None, exc
        else:
            outcome, reason = DispatchOutcome.ACCEPTED, None
        settled = await self.run_service.settle(
            SettleRunCommand(run_id=run.run_id, outcome=outcome, reason=reason, replied_at=UTC_NOW)
        )
        # Mirrors the orchestrator: the failure is raised unless the robot's own frames have moved
        # the run on, so an unconfirmed run is reported rather than read as a started one.
        if error is not None and settled.status in (
            RunStatus.PENDING,
            RunStatus.REJECTED,
            RunStatus.FAILED,
        ):
            raise error
        return settled

    async def robot_reports(
        self, run: MissionRun, exec_status: MissionExecStatus, *, header_id: int = 1, robot=None
    ) -> None:
        await self.state_service.record(
            RecordMissionStateCommand(
                robot_id=robot or run.robot_id,
                state=MissionStateMessage(
                    run_id=run.run_id,
                    header_id=header_id,
                    timestamp=UTC_NOW,
                    exec_status=exec_status,
                    current_stage_index=0,
                ),
            )
        )

    async def complete(self, run: MissionRun) -> MissionRun:
        await self.robot_reports(run, MissionExecStatus.RUNNING, header_id=1)
        await self.robot_reports(run, MissionExecStatus.SUCCEEDED, header_id=2)
        return await self.runs.get(run.run_id)

    def lifecycle(self) -> list[dict]:
        return [p for t, p, _ in self.events.published if t.endswith("/lifecycle")]

    def latched_state(self) -> dict:
        frames = [p for t, p, latch in self.events.published if t.endswith("/state") and latch]
        return frames[-1]


# re-running a mission


@pytest.mark.asyncio
async def test_a_succeeded_mission_can_be_dispatched_again():
    w = _World()
    mission = await w.mission()
    first = await w.complete(await w.start(mission.mission_id))
    first_rows = await w.runs.get_stage_runs(first.run_id)

    second = await w.complete(await w.start(mission.mission_id))

    assert second.run_id != first.run_id
    assert {first.status, second.status} == {RunStatus.SUCCEEDED}
    assert first.stages_digest == second.stages_digest
    again = await w.runs.get(first.run_id)
    assert again.stages == first.stages and again.stages_digest == first.stages_digest
    assert await w.runs.get_stage_runs(first.run_id) == first_rows
    latest = await w.runs.latest_by_mission([mission.mission_id])
    assert latest[mission.mission_id].run_id == second.run_id


@pytest.mark.asyncio
async def test_a_failed_mission_can_be_run_again_without_losing_the_failure():
    w = _World()
    mission = await w.mission()
    first = await w.start(mission.mission_id)
    await w.robot_reports(first, MissionExecStatus.RUNNING)
    await w.robot_reports(first, MissionExecStatus.FAILED, header_id=2)
    failed = await w.runs.get(first.run_id)
    assert failed.status is RunStatus.FAILED

    second = await w.start(mission.mission_id)

    assert second.status is RunStatus.DISPATCHED
    assert (await w.runs.get(first.run_id)).status is RunStatus.FAILED


@pytest.mark.asyncio
async def test_the_same_mission_can_run_on_a_different_robot_later():
    w = _World()
    mission = await w.mission()
    first = await w.complete(await w.start(mission.mission_id, ROBOT_A))
    second = await w.start(mission.mission_id, ROBOT_B)
    assert (first.robot_id, second.robot_id) == (ROBOT_A, ROBOT_B)
    assert w.dispatcher.dispatched[-1][2] == ROBOT_B


# start: the two phases


@pytest.mark.asyncio
async def test_prepare_commits_a_pending_run_and_publishes_an_all_waiting_frame():
    w = _World()
    mission = await w.mission(_stage(52.3), _stage(52.4))

    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )

    assert run.status is RunStatus.PENDING
    assert run.stages == mission.stages
    frame = w.latched_state()
    assert frame["run_id"] == str(run.run_id)
    assert frame["mission_id"] == str(mission.mission_id)
    assert [s["status"] for s in frame["stage_states"]] == ["WAITING", "WAITING"]
    assert w.dispatcher.dispatched == []
    (audit,) = [c for c in w.audit_calls if c["action"] == "mission.dispatch"]
    assert audit["target_id"] == str(mission.mission_id)
    assert audit["payload"]["run_id"] == str(run.run_id)


@pytest.mark.asyncio
async def test_an_accepted_dispatch_lands_dispatched_with_a_lifecycle_event():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    assert run.status is RunStatus.DISPATCHED
    assert run.dispatched_at == UTC_NOW
    assert w.dispatcher.dispatched[0][0] == run.run_id
    assert w.lifecycle()[-1] == {
        "mission_id": str(mission.mission_id),
        "run_id": str(run.run_id),
        "robot_id": ROBOT_A,
        "status": "DISPATCHED",
        "trigger": "accept",
    }


@pytest.mark.asyncio
async def test_a_rejected_dispatch_leaves_a_durable_rejected_run():
    w = _World()
    mission = await w.mission()

    async def reject(run_id, stages, robot_id):
        raise MissionRejectedByRobot(run_id, robot_id, "manual rejection")

    w.dispatcher.set_dispatch_handler(reject)
    with pytest.raises(MissionRejectedByRobot):
        await w.start(mission.mission_id)

    (run,) = await w.runs.list_by_mission(mission.mission_id)
    assert run.status is RunStatus.REJECTED
    full = await w.runs.get(run.run_id)
    assert full.failure_errors[0].type == "dispatch_rejected"
    assert full.failure_errors[0].description == "manual rejection"
    assert w.latched_state()["stage_states"][0]["status"] == "SKIPPED"
    (failed,) = [c for c in w.audit_calls if c["action"] == "mission.dispatch_failed"]
    assert failed["payload"]["error_type"] == "dispatch_rejected"


@pytest.mark.asyncio
async def test_a_timed_out_dispatch_leaves_the_run_live_and_its_robot_held():
    """A rejection means the robot is not driving; a timeout means it may be. Kept apart."""
    w = _World()
    mission = await w.mission()

    async def timeout(run_id, stages, robot_id):
        raise MissionDispatchTimeout(run_id, robot_id)

    w.dispatcher.set_dispatch_handler(timeout)
    # The caller is told the dispatch was never acknowledged, rather than that it succeeded.
    with pytest.raises(MissionDispatchTimeout):
        await w.start(mission.mission_id)

    (run,) = await w.runs.list_by_mission(mission.mission_id)
    assert run.status is RunStatus.PENDING
    stored = await w.runs.get(run.run_id)
    assert stored.failure_errors[0].type == "dispatch_timeout"
    assert stored.failure_errors[0].severity is ErrorSeverity.WARNING
    # The robot keeps the run, so nothing else can be sent to a machine that may be driving.
    assert [r.run_id for r in await w.runs.list_active_by_robot(ROBOT_A)] == [run.run_id]


@pytest.mark.asyncio
async def test_a_dispatch_call_that_blows_up_leaves_the_run_for_the_robot_to_settle():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    settled = await w.run_service.settle(
        SettleRunCommand(
            run_id=run.run_id,
            outcome=DispatchOutcome.ERROR,
            reason="ZenohError: session closed",
            replied_at=UTC_NOW,
        )
    )
    assert settled.status is RunStatus.PENDING
    assert settled.failure_errors[0].type == "dispatch_error"
    assert "session closed" in settled.failure_errors[0].description
    # The robot is still held: the transport failed, which says nothing about the machine.
    assert [r.run_id for r in await w.runs.list_active_by_robot(ROBOT_A)] == [run.run_id]


@pytest.mark.asyncio
async def test_dispatch_uses_the_assigned_robot_when_none_is_named():
    w = _World()
    mission = await w.mission()
    await w.missions.set_assigned_robot(mission.mission_id, ROBOT_B)
    run = await w.start(mission.mission_id, robot_id=None)
    assert run.robot_id == ROBOT_B
    # Dispatch never writes the default; assign is its only writer.
    assert (await w.missions.get(mission.mission_id)).assigned_robot_id == ROBOT_B


@pytest.mark.asyncio
async def test_dispatch_refuses_when_no_robot_is_known():
    w = _World()
    mission = await w.mission()
    with pytest.raises(NoRobotAssigned):
        await w.start(mission.mission_id, robot_id=None)


@pytest.mark.asyncio
async def test_dispatch_refuses_an_archived_mission():
    w = _World()
    mission = await w.mission()
    await w.complete(await w.start(mission.mission_id))
    await w.mission_service.delete(DeleteMissionCommand(mission_id=mission.mission_id))
    with pytest.raises(MissionArchived):
        await w.start(mission.mission_id)


@pytest.mark.asyncio
async def test_dispatch_rejects_when_factsheet_missing():
    w = _World()
    mission = await w.mission()
    with pytest.raises(RobotFactsheetMissing):
        await w.start(mission.mission_id, robot_id="unknown-robot")


@pytest.mark.asyncio
async def test_dispatch_rejects_unsupported_stage_kind():
    w = _World()
    w.factsheets.set(RobotFactsheet(robot_id=ROBOT_A, navigation=None))
    mission = await w.mission()
    with pytest.raises(UnsupportedStageKind):
        await w.start(mission.mission_id)


# concurrency


@pytest.mark.asyncio
async def test_a_second_run_of_an_active_mission_is_refused_by_default():
    w = _World()
    mission = await w.mission()
    first = await w.start(mission.mission_id, ROBOT_A)
    with pytest.raises(MissionRunInProgress) as excinfo:
        await w.start(mission.mission_id, ROBOT_B)
    assert excinfo.value.active_run_ids == [first.run_id]
    assert len(await w.runs.list_by_mission(mission.mission_id)) == 1


@pytest.mark.asyncio
async def test_allow_concurrent_starts_a_second_run_on_another_robot():
    w = _World()
    mission = await w.mission()
    first = await w.start(mission.mission_id, ROBOT_A)
    second = await w.start(mission.mission_id, ROBOT_B, allow_concurrent=True)
    active = await w.runs.list_active_by_mission(mission.mission_id)
    assert {r.run_id for r in active} == {first.run_id, second.run_id}


@pytest.mark.asyncio
async def test_allow_concurrent_on_the_same_robot_is_robot_busy():
    w = _World()
    mission = await w.mission()
    await w.start(mission.mission_id, ROBOT_A)
    with pytest.raises(RobotBusy):
        await w.start(mission.mission_id, ROBOT_A, allow_concurrent=True)


@pytest.mark.asyncio
async def test_a_robot_running_another_mission_is_busy():
    w = _World()
    first, second = await w.mission(), await w.mission()
    await w.start(first.mission_id, ROBOT_A)
    with pytest.raises(RobotBusy) as excinfo:
        await w.start(second.mission_id, ROBOT_A)
    assert excinfo.value.active_mission_id == first.mission_id


@pytest.mark.asyncio
async def test_cancel_with_two_active_runs_needs_a_run_id():
    w = _World()
    mission = await w.mission()
    a = await w.start(mission.mission_id, ROBOT_A)
    b = await w.start(mission.mission_id, ROBOT_B, allow_concurrent=True)
    with pytest.raises(AmbiguousRun) as excinfo:
        await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))
    assert set(excinfo.value.active_run_ids) == {a.run_id, b.run_id}

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id, run_id=b.run_id))

    assert (await w.runs.get(b.run_id)).status is RunStatus.CANCELLING
    assert (await w.runs.get(a.run_id)).status is RunStatus.DISPATCHED
    assert w.dispatcher.cancelled[-1][:2] == (b.run_id, ROBOT_B)


@pytest.mark.asyncio
async def test_a_run_id_of_another_mission_is_not_found():
    w = _World()
    first, second = await w.mission(), await w.mission()
    run = await w.start(first.mission_id, ROBOT_A)
    with pytest.raises(RunNotFoundError):
        await w.run_service.cancel(
            CancelRunCommand(mission_id=second.mission_id, run_id=run.run_id)
        )


# after completion, no refusal


@pytest.mark.asyncio
async def test_dispatch_after_completion_without_override_starts_a_second_run():
    """No active run, so nothing refuses: a replayed approval after the run has ended starts
    another. The idempotency key that would catch it belongs to the chat write path."""
    w = _World()
    mission = await w.mission()
    await w.complete(await w.start(mission.mission_id))
    second = await w.start(mission.mission_id)
    assert second.status is RunStatus.DISPATCHED
    assert len(await w.runs.list_by_mission(mission.mission_id)) == 2


# cancel during PENDING


@pytest.mark.asyncio
async def test_cancel_during_pending_commands_the_robot():
    w = _World()
    mission = await w.mission()
    w.dispatcher.gate = asyncio.Event()

    started = asyncio.create_task(w.start(mission.mission_id))
    await asyncio.sleep(0)  # let prepare commit and the dispatcher block on the gate
    (pending,) = await w.runs.list_active_by_mission(mission.mission_id)
    assert pending.status is RunStatus.PENDING

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))
    w.dispatcher.gate.set()
    settled = await started

    # The robot accepted the goal after the cancel was written, so the cancel is sent again and
    # the run stays CANCELLING until the robot reports the end.
    assert settled.status is RunStatus.CANCELLING
    assert (await w.runs.get(pending.run_id)).status is RunStatus.CANCELLING
    assert [c[0] for c in w.dispatcher.cancelled] == [pending.run_id, pending.run_id]
    assert "RUNNING" not in [e["status"] for e in w.lifecycle()]


@pytest.mark.asyncio
async def test_a_cancel_that_landed_mid_dispatch_is_resent_when_the_reply_times_out():
    # The first cancel may have raced ahead of the dispatch, so the robot can hold a goal for a
    # run the backend already cancelled. Settling an unanswered reply re-sends it.
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))
    w.dispatcher.cancelled.clear()

    settled = await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.TIMEOUT, replied_at=UTC_NOW)
    )

    assert settled.status is RunStatus.CANCELLING
    assert [c[:2] for c in w.dispatcher.cancelled] == [(run.run_id, ROBOT_A)]


@pytest.mark.asyncio
async def test_an_unanswered_reply_does_not_mark_a_run_the_robot_is_already_driving():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.robot_reports(run, MissionExecStatus.RUNNING)

    settled = await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.TIMEOUT, replied_at=UTC_NOW)
    )

    assert settled.status is RunStatus.RUNNING
    assert settled.failure_errors is None
    # The robot has the goal, so the run carries a dispatch time even though no reply arrived.
    assert settled.dispatched_at == UTC_NOW
    assert w.dispatcher.cancelled == []


@pytest.mark.asyncio
async def test_a_run_freezes_the_frame_of_every_site_it_drives_in():
    # A site-local waypoint is a pair of numbers measured against an anchor that can be moved or
    # deleted later, so the anchor is copied into the run the same way the plan is.
    w = _World()
    site = await w.sites.create(
        name="Barn",
        anchor_lat=52.3,
        anchor_lon=8.05,
        anchor_heading_deg=90.0,
        nav2_map_ref="barn.yaml",
        outline=None,
        description=None,
    )
    mission = await w.mission(_site_local_stage(site.site_id))

    run = await w.start(mission.mission_id)

    frozen = (await w.runs.get(run.run_id)).site_anchors
    assert frozen is not None
    anchor = frozen[str(site.site_id)]
    assert (anchor.name, anchor.anchor_lat, anchor.anchor_lon) == ("Barn", 52.3, 8.05)

    # Moving the site afterwards leaves the run's copy untouched.
    await w.sites.update(site_id=site.site_id, anchor_lat=52.9)
    after = (await w.runs.get(run.run_id)).site_anchors[str(site.site_id)]
    assert after.anchor_lat == 52.3


@pytest.mark.asyncio
async def test_a_run_with_no_site_local_waypoints_freezes_no_anchors():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    assert (await w.runs.get(run.run_id)).site_anchors is None


@pytest.mark.asyncio
async def test_each_coverage_stage_is_guarded_on_its_own_parameters():
    # Two stages of one mission need not have been laid out for the same machine, so a robot that
    # fits the first and not the second must still be refused.
    w = _World()
    w.factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_A,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(track_width_m=1.0, min_turning_radius_m=1.0),
        )
    )
    field = seeded_field(w.fields)
    mission = await w.mission_service.create_generated(
        CreateGeneratedMissionCommand(
            name="two fields",
            stages=[
                coverage_stage_for(field, turning_radius_m=1.5),
                coverage_stage_for(field, lat=52.9, turning_radius_m=0.5),
            ],
        )
    )

    with pytest.raises(IncompatibleTurningRadius):
        await w.start(mission.mission_id)


@pytest.mark.asyncio
async def test_a_run_freezes_each_coverage_stage_with_its_provenance():
    w = _World()
    w.factsheets.set(
        RobotFactsheet(
            robot_id=ROBOT_A,
            navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            coverage=CoverageCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
            physical_parameters=PhysicalParameters(track_width_m=1.0, min_turning_radius_m=0.0),
        )
    )
    field = seeded_field(w.fields)
    planned = coverage_stage_for(field)
    mission = await w.mission_service.create_generated(
        CreateGeneratedMissionCommand(name="cover", stages=[planned])
    )

    run = await w.start(mission.mission_id)

    frozen = (await w.runs.get(run.run_id)).stages[0]
    assert frozen.provenance == planned.provenance


# settle when a frame beat the reply


@pytest.mark.asyncio
async def test_the_robots_first_frame_can_beat_the_dispatch_reply():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    assert (await w.runs.get(run.run_id)).status is RunStatus.RUNNING

    settled = await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.ACCEPTED, replied_at=UTC_NOW)
    )

    assert settled.status is RunStatus.RUNNING
    assert settled.dispatched_at == UTC_NOW


@pytest.mark.asyncio
async def test_a_short_run_that_finishes_before_the_reply_is_not_stranded():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.robot_reports(run, MissionExecStatus.SUCCEEDED)
    settled = await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.ACCEPTED, replied_at=UTC_NOW)
    )
    assert settled.status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_timeout_whose_robot_is_reporting_keeps_the_run_running():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    settled = await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.TIMEOUT, replied_at=UTC_NOW)
    )
    assert settled.status is RunStatus.RUNNING
    assert w.dispatcher.cancelled == []


# steering


@pytest.mark.asyncio
async def test_cancel_running_calls_dispatcher_and_resolves_stages():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    cancelling = await w.runs.get(run.run_id)
    assert cancelling.status is RunStatus.CANCELLING
    assert w.dispatcher.cancelled[-1][:2] == (run.run_id, ROBOT_A)
    request = cancelling.transitions[-1]
    assert request.trigger is RunTrigger.CANCEL_REQUEST
    assert request.actor == "operator"
    assert request.acknowledged is True
    assert request.report_header_id == 1

    await w.robot_reports(run, MissionExecStatus.CANCELLED, header_id=2)

    cancelled = await w.runs.get(run.run_id)
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.transitions[-1].actor == "robot"
    # The stage never reported anything but WAITING, so it resolves to SKIPPED.
    assert w.latched_state()["stage_states"][0]["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_pause_and_resume_call_the_dispatcher():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)

    await w.run_service.pause(PauseRunCommand(mission_id=mission.mission_id))
    pausing = await w.runs.get(run.run_id)
    assert pausing.status is RunStatus.PAUSING
    assert w.dispatcher.paused == [(run.run_id, ROBOT_A)]
    assert pausing.transitions[-1].acknowledged is True

    # A PAUSED report that predates the request does not confirm it.
    await w.robot_reports(run, MissionExecStatus.PAUSED, header_id=1)
    assert (await w.runs.get(run.run_id)).status is RunStatus.PAUSING
    await w.robot_reports(run, MissionExecStatus.PAUSED, header_id=2)
    assert (await w.runs.get(run.run_id)).status is RunStatus.PAUSED

    await w.run_service.resume(ResumeRunCommand(mission_id=mission.mission_id))
    assert (await w.runs.get(run.run_id)).status is RunStatus.RESUMING
    assert w.dispatcher.resumed == [(run.run_id, ROBOT_A)]
    await w.robot_reports(run, MissionExecStatus.RUNNING, header_id=3)
    assert (await w.runs.get(run.run_id)).status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_a_pause_the_robot_does_not_answer_writes_nothing():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    w.dispatcher.replies["pause"] = ControlReply(applied=None)

    with pytest.raises(RobotUnreachable):
        await w.run_service.pause(PauseRunCommand(mission_id=mission.mission_id))

    after = await w.runs.get(run.run_id)
    assert after.status is RunStatus.RUNNING
    assert all(t.trigger is not RunTrigger.PAUSE_REQUEST for t in after.transitions)


@pytest.mark.asyncio
async def test_a_refused_pause_writes_nothing_and_names_the_reason():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    w.dispatcher.replies["pause"] = ControlReply(
        applied=False, refusal=ControlRefusal.OTHER, reason="estop latched"
    )

    with pytest.raises(RobotRefusedControl, match="estop latched"):
        await w.run_service.pause(PauseRunCommand(mission_id=mission.mission_id))
    assert (await w.runs.get(run.run_id)).status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_a_cancel_the_robot_does_not_answer_stays_cancelling_unacknowledged():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    w.dispatcher.replies["cancel"] = ControlReply(applied=None)

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    after = await w.runs.get(run.run_id)
    assert after.status is RunStatus.CANCELLING
    assert after.transitions[-1].acknowledged is False


@pytest.mark.asyncio
async def test_a_cancel_refused_as_not_executing_closes_the_run():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    w.dispatcher.replies["cancel"] = ControlReply(
        applied=False, refusal=ControlRefusal.NOT_EXECUTING_RUN, reason="not executing this run"
    )

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    after = await w.runs.get(run.run_id)
    assert after.status is RunStatus.CANCELLED
    assert after.transitions[-1].trigger is RunTrigger.RECONCILE
    assert after.transitions[-1].actor == "backend"
    assert w.latched_state()["stage_states"][0]["status"] == "SKIPPED"


@pytest.mark.asyncio
async def test_a_cancel_refused_for_another_reason_stays_cancelling():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    w.dispatcher.replies["cancel"] = ControlReply(
        applied=False, refusal=ControlRefusal.OTHER, reason="estop latched"
    )

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    after = await w.runs.get(run.run_id)
    assert after.status is RunStatus.CANCELLING
    assert after.transitions[-1].acknowledged is False
    assert after.transitions[-1].detail == {"mode": "graceful", "reason": "estop latched"}


@pytest.mark.asyncio
async def test_a_cancel_while_cancelling_is_sent_again_and_recorded():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)

    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))
    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    after = await w.runs.get(run.run_id)
    assert after.status is RunStatus.CANCELLING
    assert [t.trigger for t in after.transitions[-2:]] == [
        RunTrigger.CANCEL_REQUEST,
        RunTrigger.CANCEL_REQUEST,
    ]
    assert len(w.dispatcher.cancelled) == 2


@pytest.mark.asyncio
async def test_a_succeeded_report_ends_a_cancelling_run_as_succeeded():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)
    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    await w.robot_reports(run, MissionExecStatus.SUCCEEDED, header_id=2)

    assert (await w.runs.get(run.run_id)).status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_a_rejected_dispatch_while_cancelling_ends_cancelled():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))

    settled = await w.run_service.settle(
        SettleRunCommand(
            run_id=run.run_id, outcome=DispatchOutcome.REJECTED, reason="busy", replied_at=UTC_NOW
        )
    )

    assert settled.status is RunStatus.CANCELLED
    assert (await w.runs.get(run.run_id)).transitions[-1].trigger is RunTrigger.REJECT


@pytest.mark.asyncio
async def test_cancel_after_terminal_raises():
    w = _World()
    mission = await w.mission()
    await w.complete(await w.start(mission.mission_id))
    with pytest.raises(InvalidMissionTransition):
        await w.run_service.cancel(CancelRunCommand(mission_id=mission.mission_id))


@pytest.mark.asyncio
async def test_pause_of_a_pending_run_is_refused():
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    with pytest.raises(InvalidMissionTransition):
        await w.run_service.pause(PauseRunCommand(mission_id=mission.mission_id, run_id=run.run_id))


# the latched frame


@pytest.mark.asyncio
async def test_dispatch_replaces_the_latched_state_frame():
    w = _World()
    mission = await w.mission()
    first = await w.complete(await w.start(mission.mission_id))
    assert w.latched_state()["run_id"] == str(first.run_id)
    assert w.latched_state()["stage_states"][0]["status"] == "FINISHED"

    second = await w.start(mission.mission_id)

    frame = w.latched_state()
    assert frame["run_id"] == str(second.run_id)
    assert [s["status"] for s in frame["stage_states"]] == ["WAITING"]


# annotation, snapshot, stage ids


@pytest.mark.asyncio
async def test_a_run_can_be_annotated_at_any_time():
    w = _World()
    mission = await w.mission()
    run = await w.complete(await w.start(mission.mission_id, notes="first go"))
    assert run.notes == "first go"
    annotated = await w.run_service.annotate(
        AnnotateRunCommand(run_id=run.run_id, notes="dew on the sensors")
    )
    assert annotated.notes == "dew on the sensors"
    assert w.audit_calls[-1]["action"] == "run.annotate"


@pytest.mark.asyncio
async def test_editing_the_mission_never_changes_a_finished_run():
    w = _World()
    mission = await w.mission(_stage(52.3))
    run = await w.complete(await w.start(mission.mission_id))
    kept = mission.stages[0].stage_id

    await w.mission_service.update(
        UpdateMissionCommand(
            mission_id=mission.mission_id,
            stages=[
                NavigationStageInput(stage_id=kept, waypoints=[WGS84Waypoint(lat=53.0, lon=8.05)]),
                _stage(52.9),
            ],
        )
    )

    again = await w.runs.get(run.run_id)
    assert again.stages == run.stages
    assert again.stages[0].waypoints[0].lat == 52.3
    edited = await w.missions.get(mission.mission_id)
    assert edited.stages[0].stage_id == kept
    assert stages_digest(edited.stages) != run.stages_digest


@pytest.mark.asyncio
async def test_a_frame_from_the_wrong_robot_is_dropped():
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id, ROBOT_A)
    await w.robot_reports(run, MissionExecStatus.SUCCEEDED, robot=ROBOT_B)
    assert (await w.runs.get(run.run_id)).status is RunStatus.DISPATCHED


# deleting finished runs


@pytest.mark.asyncio
async def test_a_finished_run_can_be_deleted_and_the_attempt_stays_in_the_audit_log():
    from leitstand_backend.ports.inbound.run_management import DeleteRunCommand

    w = _World()
    mission = await w.mission()
    run = await w.complete(await w.start(mission.mission_id))
    await w.runs.upsert_stage_runs(run.run_id, await w.runs.get_stage_runs(run.run_id))

    await w.run_service.delete(DeleteRunCommand(run_id=run.run_id))

    assert await w.runs.get(run.run_id) is None
    assert await w.runs.get_stage_runs(run.run_id) == []
    assert (await w.runs.latest_by_mission([mission.mission_id])) == {}
    deleted = [c for c in w.audit_calls if c["action"] == "run.delete"]
    assert deleted and deleted[0]["payload"]["run_id"] == str(run.run_id)
    assert [c for c in w.audit_calls if c["action"] == "mission.dispatch"]


@pytest.mark.asyncio
async def test_an_active_run_cannot_be_deleted():
    from leitstand_backend.ports.inbound.run_management import DeleteRunCommand

    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    with pytest.raises(InvalidMissionTransition):
        await w.run_service.delete(DeleteRunCommand(run_id=run.run_id))
    assert (await w.runs.get(run.run_id)) is not None


@pytest.mark.asyncio
async def test_deleting_an_unknown_run_is_not_found():
    from uuid import uuid4

    from leitstand_backend.ports.inbound.run_management import DeleteRunCommand

    w = _World()
    with pytest.raises(RunNotFoundError):
        await w.run_service.delete(DeleteRunCommand(run_id=uuid4()))


# lifecycle edge cases


@pytest.mark.asyncio
async def test_a_dispatched_run_pauses_when_its_running_frame_was_lost():
    """Without this the run holds DISPATCHED for good and only a cancel frees the robot."""
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    assert run.status is RunStatus.DISPATCHED

    await w.robot_reports(run, MissionExecStatus.PAUSED, header_id=1)
    assert (await w.runs.get(run.run_id)).status is RunStatus.PAUSED

    resumed = await w.run_service.resume(
        ResumeRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
    )
    assert resumed.status is RunStatus.RESUMING
    await w.robot_reports(run, MissionExecStatus.RUNNING, header_id=2)
    assert (await w.runs.get(run.run_id)).status is RunStatus.RUNNING


@pytest.mark.asyncio
async def test_a_run_that_succeeds_after_an_unacknowledged_dispatch_reports_no_failure():
    """The dispatch warning describes the reply, not the outcome, so success must clear it."""
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.TIMEOUT, replied_at=UTC_NOW)
    )
    assert (await w.runs.get(run.run_id)).failure_errors

    await w.complete(run)

    settled = await w.runs.get(run.run_id)
    assert settled.status is RunStatus.SUCCEEDED
    assert settled.failure_errors is None


@pytest.mark.asyncio
async def test_a_failed_run_keeps_the_dispatch_cause_beside_the_robots_own():
    """Both are kept: the reply never arrived, and the robot then reported why it failed."""
    w = _World()
    mission = await w.mission()
    run = await w.run_service.prepare(
        StartRunCommand(mission_id=mission.mission_id, robot_id=ROBOT_A, origin=ORIGIN)
    )
    await w.run_service.settle(
        SettleRunCommand(run_id=run.run_id, outcome=DispatchOutcome.TIMEOUT, replied_at=UTC_NOW)
    )
    await w.robot_reports(run, MissionExecStatus.FAILED, header_id=2)

    failed = await w.runs.get(run.run_id)
    assert failed.status is RunStatus.FAILED
    assert [e.type for e in failed.failure_errors or []][0] == "dispatch_timeout"


@pytest.mark.asyncio
async def test_deleting_a_missions_last_run_drops_its_latched_state_frame():
    """Left latched, it would show a finished run on a mission that reads as never run."""
    from leitstand_backend.ports.inbound.run_management import DeleteRunCommand

    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.complete(run)
    topic = mission_topic(mission.mission_id, "state")
    assert topic not in w.events.unlatched

    await w.run_service.delete(DeleteRunCommand(run_id=run.run_id))

    assert topic in w.events.unlatched


@pytest.mark.asyncio
async def test_deleting_one_of_several_runs_leaves_the_latch_alone():
    """Another run remains, so the latched frame stays."""
    from leitstand_backend.ports.inbound.run_management import DeleteRunCommand

    w = _World()
    mission = await w.mission()
    first = await w.start(mission.mission_id)
    await w.complete(first)
    second = await w.start(mission.mission_id)
    await w.complete(second)

    await w.run_service.delete(DeleteRunCommand(run_id=first.run_id))

    assert mission_topic(mission.mission_id, "state") not in w.events.unlatched


@pytest.mark.asyncio
async def test_the_cancel_mode_reaches_the_robot():
    """The mode a caller chooses must reach the dispatcher."""
    w = _World()
    mission = await w.mission()
    run = await w.start(mission.mission_id)
    await w.robot_reports(run, MissionExecStatus.RUNNING)

    await w.run_service.cancel(
        CancelRunCommand(mission_id=mission.mission_id, mode=CancelMode.IMMEDIATE)
    )

    assert w.dispatcher.cancelled == [(run.run_id, ROBOT_A, CancelMode.IMMEDIATE)]


@pytest.mark.asyncio
async def test_a_run_is_refused_for_a_robot_the_fleet_knows_to_be_offline():
    """A dispatch to an offline robot would only time out and leave a pending run behind."""
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    await world.robots.record_offline(ROBOT_A)
    mission = await world.mission()

    with pytest.raises(RobotOffline):
        await world.start(mission.mission_id, ROBOT_A)

    assert await world.runs.list_active_by_robot(ROBOT_A) == []
    assert world.dispatcher.dispatched == []


@pytest.mark.asyncio
async def test_a_robot_the_fleet_has_never_seen_is_judged_by_its_factsheet_alone():
    """No robot row means no online verdict; the factsheet check answers for an unknown id."""
    world = _World()
    mission = await world.mission()

    run = await world.start(mission.mission_id, ROBOT_A)

    assert run.status is RunStatus.DISPATCHED


@pytest.mark.asyncio
async def test_cancelling_a_run_whose_robot_is_offline_does_not_wait_for_it():
    """The cancel is written and left to the reconnect reconciliation; no RPC, no timeout."""
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    mission = await world.mission()
    run = await world.start(mission.mission_id, ROBOT_A)
    await world.robots.record_offline(ROBOT_A)
    world.dispatcher.cancelled.clear()

    cancelled = await world.run_service.cancel(
        CancelRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
    )

    assert cancelled.status is RunStatus.CANCELLING
    assert world.dispatcher.cancelled == []
    assert cancelled.transitions[-1].acknowledged is False
    assert cancelled.transitions[-1].detail["deferred"] == "robot offline"


@pytest.mark.asyncio
async def test_pausing_a_run_whose_robot_is_offline_is_refused_at_once():
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    mission = await world.mission()
    run = await world.start(mission.mission_id, ROBOT_A)
    await world.robots.record_offline(ROBOT_A)

    with pytest.raises(RobotOffline):
        await world.run_service.pause(
            PauseRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
        )

    assert (await world.runs.get(run.run_id)).status is run.status


@pytest.mark.asyncio
async def test_an_operator_closes_a_run_whose_robot_is_offline():
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    mission = await world.mission()
    run = await world.start(mission.mission_id, ROBOT_A)
    await world.robots.record_offline(ROBOT_A)

    closed = await world.run_service.close(
        CloseRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
    )

    assert closed.status is RunStatus.CANCELLED
    assert closed.ended_at is not None
    stored = await world.runs.get(run.run_id)
    assert stored.transitions[-1].trigger is RunTrigger.CLOSE
    assert stored.transitions[-1].actor == "operator"
    assert world.dispatcher.cancelled == []
    assert await world.runs.list_active_by_robot(ROBOT_A) == []
    assert any(call["action"] == "run.close" for call in world.audit_calls)
    assert world.lifecycle()[-1]["status"] == "CANCELLED"
    assert world.lifecycle()[-1]["trigger"] == "close"


@pytest.mark.asyncio
async def test_closing_a_run_whose_robot_is_online_is_refused():
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    mission = await world.mission()
    run = await world.start(mission.mission_id, ROBOT_A)

    with pytest.raises(RobotOnline):
        await world.run_service.close(
            CloseRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
        )

    assert (await world.runs.get(run.run_id)).status is RunStatus.DISPATCHED


@pytest.mark.asyncio
async def test_a_finished_run_cannot_be_closed():
    world = _World()
    await world.robots.record_online(ROBOT_A, Metadata(id=ROBOT_A))
    mission = await world.mission()
    run = await world.start(mission.mission_id, ROBOT_A)
    await world.complete(run)
    await world.robots.record_offline(ROBOT_A)

    with pytest.raises(InvalidMissionTransition):
        await world.run_service.close(
            CloseRunCommand(mission_id=mission.mission_id, run_id=run.run_id)
        )
