"""Mission definition CRUD and the mission-addressed run verbs."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from leitstand_backend.adapters.inbound.web.missions.dto import (
    CancelBody,
    MissionAssignBody,
    MissionCoverageCreate,
    MissionCreate,
    MissionDispatchBody,
    MissionUpdate,
    MissionView,
    RunSelectBody,
)
from leitstand_backend.adapters.inbound.web.missions.mappers import (
    to_assign_command,
    to_cancel_command,
    to_close_command,
    to_create_command,
    to_delete_command,
    to_mission_view,
    to_pause_command,
    to_plan_coverage_command,
    to_restore_command,
    to_resume_command,
    to_start_run_command,
    to_unassign_command,
    to_update_command,
)
from leitstand_backend.application.coverage_stage_planner import PLANNING_INPUT_ERRORS
from leitstand_backend.application.run_state_view import RunStateView, build_run_state_view
from leitstand_backend.domain.errors import (
    AmbiguousRun,
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    DuplicateStageId,
    FieldNotFoundError,
    FieldNotPlannable,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionArchived,
    MissionDispatchFailed,
    MissionDispatchTimeout,
    MissionNotArchived,
    MissionNotFoundError,
    MissionRejectedByRobot,
    MissionRunInProgress,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RobotOffline,
    RobotOnline,
    RobotPhysicalParametersMissing,
    RobotRefusedControl,
    RobotUnreachable,
    RunNotFoundError,
    StageNotHomogeneous,
    StageNotInMission,
    StageSpansSites,
    StaleCoverageBoundary,
    UnknownSite,
    UnknownSiteForRobot,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.domain.model.mission.mission_run import MissionRunSummary
from leitstand_backend.domain.model.mission.stage_state_record import waiting_stage_statuses
from leitstand_backend.infrastructure.deps import (
    current_run_origin,
    get_coverage_planning_use_case,
    get_mission_management_use_case,
    get_mission_repository,
    get_mission_run_repository,
    get_run_management_use_case,
    get_run_start_use_case,
)
from leitstand_backend.ports.inbound.coverage_planning import CoveragePlanningUseCase
from leitstand_backend.ports.inbound.mission_management import MissionManagementUseCase
from leitstand_backend.ports.inbound.run_management import RunManagementUseCase
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])

# A body naming a field or robot that cannot be planned for is unprocessable, as an unknown site is.
_AUTHORING_ERRORS = (
    StageNotHomogeneous,
    StageSpansSites,
    UnknownSite,
    DuplicateStageId,
    StageNotInMission,
    FieldNotFoundError,
    *PLANNING_INPUT_ERRORS,
)
_ROBOT_FIT_ERRORS = (
    StageNotHomogeneous,
    UnsupportedStageKind,
    UnknownSiteForRobot,
    UnsupportedWaypointFrame,
    IncompatibleTurningRadius,
    ImplementNarrowerThanRobot,
    RobotPhysicalParametersMissing,
    StaleCoverageBoundary,
)


async def _view(
    mission_id: UUID, repo: MissionRepository, runs: MissionRunRepository
) -> MissionView:
    record = await repo.get(mission_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    latest = await runs.latest_by_mission([mission_id])
    active = await runs.list_active_by_mission(mission_id)
    return to_mission_view(record, latest.get(mission_id), active)


@router.get("/", response_model=list[MissionView], operation_id="list_missions")
async def list_missions(
    robot: str | None = Query(
        default=None, description="Filter to missions whose default robot is this robot id."
    ),
    name: str | None = Query(
        default=None,
        description="Filter to missions with exactly this name, matched case-insensitively.",
    ),
    include_archived: bool = Query(default=False, description="Also return archived missions."),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> list[MissionView]:
    """List missions newest first, each with its latest run.

    A mission is a definition that can be run any number of times; ``latest_run`` carries the
    status, robot and timings of its most recent run and is null when it has never run. Its
    status is one of PENDING, DISPATCHED, RUNNING, PAUSED, SUCCEEDED, FAILED, CANCELLED or
    REJECTED. Optionally filter by robot, by name, or both. This returns definitions, not live
    per-stage progress: use get_mission_state for that.
    """
    missions = await repo.list(robot_id=robot, name=name, include_archived=include_archived)
    latest = await runs.latest_by_mission([m.mission_id for m in missions])
    active: dict[UUID, list[MissionRunSummary]] = defaultdict(list)
    for run in await runs.list_active():
        active[run.mission_id].append(run)
    return [
        to_mission_view(m, latest.get(m.mission_id), active.get(m.mission_id, ())) for m in missions
    ]


@router.get("/{mission_id}", response_model=MissionView, operation_id="get_mission")
async def get_mission(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Return one mission's definition, default robot and latest run.

    Identify the mission by mission_id from list_missions, which is also how a mission named by
    the operator is resolved. Its run history is list_mission_runs.
    """
    return await _view(mission_id, repo, runs)


@router.get("/{mission_id}/state", response_model=RunStateView, operation_id="get_mission_state")
async def get_mission_state(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> RunStateView:
    """Return live per-stage progress of the mission's current run, errors attributed per stage.

    The current run is the most recently started one still active, else the latest run of any
    outcome. Each stage carries a status and a progress fraction. Use this to answer how far
    along a mission is, or why it failed. A mission that has never run has no state (404).
    """
    if await repo.get(mission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    active = await runs.list_active_by_mission(mission_id)
    summary = active[0] if active else (await runs.latest_by_mission([mission_id])).get(mission_id)
    if summary is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission has never run")
    run = await runs.get(summary.run_id)
    if run is None:
        # Deleted between listing it and reading it; the mission now has no run to report on.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission has never run")
    rows = await runs.get_stage_runs(run.run_id) or waiting_stage_statuses(
        run.stages, run.created_at
    )
    return build_run_state_view(run.run_id, run.mission_id, rows, run.failure_errors)


@router.post(
    "/",
    response_model=MissionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_mission",
)
async def create_mission(
    body: MissionCreate,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Create a mission from a name and an ordered list of stages.

    A navigation stage lists waypoints; every one must be a coordinate the operator stated. Do
    not compute one: not from a field or site boundary, not from a robot's current position, and
    not by converting a distance in metres into degrees. A coverage stage names a field, the
    implement's working width and the robot whose factsheet supplies the machine values, and the
    backend plans its path; for a whole field to be covered, prefer plan_coverage_mission, which
    takes the same inputs and names the mission after the field. If you were given an area, a row
    spacing or a bearing rather than coordinates, do not write a navigation stage: say you cannot
    work the coordinates out and ask for them.

    ``stages`` is a list of stage objects, not text containing a list.

    The backend assigns the mission and stage ids. The mission is a definition and is not
    dispatched; dispatch_mission runs it, as many times as wanted.
    """
    try:
        mission = await uc.create(to_create_command(body))
    except _AUTHORING_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except CoveragePlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.post(
    "/coverage",
    response_model=MissionView,
    status_code=status.HTTP_201_CREATED,
    operation_id="plan_coverage_mission",
)
async def plan_coverage_mission(
    body: MissionCoverageCreate,
    uc: CoveragePlanningUseCase = Depends(get_coverage_planning_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Create a mission that covers a whole field, planned from the field's own boundary.

    Use this whenever the operator asks to cover, survey, mow or treat a field rather than to
    drive to stated points. Give the field_id, the robot the plan is for, and the working width
    in metres; supply no coordinates, because the path is computed from the stored boundary, and
    no turning radius, because the robot declares its own.

    With ``replan`` set, the named mission's plan is rewritten in place instead and its id is
    returned. The mission is not dispatched. Its coverage metrics come back with it, so the
    operator can judge the plan before dispatching it.
    """
    try:
        mission = await uc.plan(to_plan_coverage_command(body))
    except FieldNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission to re-plan not found")
    except (InvalidMissionTransition, MissionArchived) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (
        CoveragePlanRejected,
        FieldNotPlannable,
        RobotFactsheetMissing,
        RobotPhysicalParametersMissing,
        StageNotHomogeneous,
        StageNotInMission,
        StageSpansSites,
        UnsupportedStageKind,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except CoveragePlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.patch("/{mission_id}", response_model=MissionView, operation_id="update_mission")
async def update_mission(
    mission_id: UUID,
    body: MissionUpdate,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Change a mission's name, description, or stages.

    Allowed at any time, even while a run is active: a run carries its own copy of the plan, so
    editing the definition never changes what a run did or is doing. Identify the mission by
    mission_id from list_missions. Supplying stages replaces the existing ones; a stage that
    carries its stage_id keeps its identity across the edit, one without gets a new id, and one
    left out is removed. A coverage stage is carried unchanged by its stage_id alone, or
    re-planned under the same id when its field, width or machine values are given again.
    """
    try:
        mission = await uc.update(to_update_command(mission_id, body))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except MissionArchived as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except _AUTHORING_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except CoveragePlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.delete(
    "/{mission_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_mission"
)
async def delete_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
) -> None:
    """Delete a mission that never ran, or archive one that did.

    A mission with runs is archived rather than deleted: it leaves the list, its runs stay
    readable, and restore_mission brings it back. A mission with a run in progress is refused.
    Identify the mission by mission_id from list_missions.
    """
    try:
        await uc.delete(to_delete_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except MissionRunInProgress as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/{mission_id}/restore", response_model=MissionView, operation_id="restore_mission")
async def restore_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Bring an archived mission back into the list. Identify it by mission_id."""
    try:
        mission = await uc.restore(to_restore_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except MissionNotArchived as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.post("/{mission_id}/assign", response_model=MissionView, operation_id="assign_mission")
async def assign_mission(
    mission_id: UUID,
    body: MissionAssignBody,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Set the robot a mission runs on by default, checking it can, without starting it.

    Identify the mission by mission_id and give the robot_id. To also start it, use
    dispatch_mission instead.
    """
    try:
        mission = await uc.assign(to_assign_command(mission_id, body.robot_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except (InvalidMissionTransition, RobotBusy, MissionArchived) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except RobotFactsheetMissing as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except _ROBOT_FIT_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.post("/{mission_id}/unassign", response_model=MissionView, operation_id="unassign_mission")
async def unassign_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Clear a mission's default robot. Identify the mission by mission_id."""
    try:
        mission = await uc.unassign(to_unassign_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except (InvalidMissionTransition, MissionArchived) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return await _view(mission.mission_id, repo, runs)


@router.post("/{mission_id}/dispatch", response_model=MissionView, operation_id="dispatch_mission")
async def dispatch_mission(
    mission_id: UUID,
    body: MissionDispatchBody | None = None,
    start_uc=Depends(get_run_start_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Start a run of a mission on a robot.

    Sends the mission's stages to its default robot, or to ``robot_id`` when given, and begins
    execution. A mission can be run any number of times; each run keeps its own record. While a
    run of this mission is active, a second one is refused (409). Identify the mission by
    ``mission_id`` from list_missions. The returned ``latest_run`` is the new run.
    """
    try:
        run = await start_uc.start(to_start_run_command(mission_id, body, current_run_origin()))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except (
        InvalidMissionTransition,
        RobotBusy,
        RobotOffline,
        MissionRunInProgress,
        MissionArchived,
    ) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (NoRobotAssigned, RobotFactsheetMissing, UnknownSite) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except _ROBOT_FIT_ERRORS as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionRejectedByRobot as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionDispatchTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    except MissionDispatchFailed as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return await _view(run.mission_id, repo, runs)


def _steer_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, MissionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    if isinstance(exc, RunNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    if isinstance(exc, RobotUnreachable):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


_STEER_ERRORS = (
    MissionNotFoundError,
    RunNotFoundError,
    RobotOffline,
    InvalidMissionTransition,
    AmbiguousRun,
    RobotUnreachable,
    RobotRefusedControl,
)


@router.post("/{mission_id}/cancel", response_model=MissionView, operation_id="cancel_mission")
async def cancel_mission(
    mission_id: UUID,
    body: CancelBody | None = None,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Cancel the mission's active run, stopping the robot.

    Identify the mission by mission_id from list_missions. With more than one run active,
    ``run_id`` says which. Both modes stop within seconds: 'graceful', the default, comes to a
    controlled stop at the next safe point and then cleans up; 'immediate' stops at once.
    """
    try:
        run = await uc.cancel(to_cancel_command(mission_id, body))
    except _STEER_ERRORS as exc:
        raise _steer_errors(exc)
    return await _view(run.mission_id, repo, runs)


@router.post("/{mission_id}/close", response_model=MissionView, operation_id="close_mission_run")
async def close_mission_run(
    mission_id: UUID,
    body: RunSelectBody | None = None,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """End a run whose robot is offline, without the robot's confirmation.

    The run becomes CANCELLED on the operator's word. Refused while the robot is online, where
    cancel_mission asks the robot and lets it confirm. If the robot later reconnects still holding
    the run, it is told to cancel. With more than one run active, ``run_id`` says which.
    """
    try:
        run = await uc.close(to_close_command(mission_id, body.run_id if body else None))
    except (*_STEER_ERRORS, RobotOnline) as exc:
        raise _steer_errors(exc)
    return await _view(run.mission_id, repo, runs)


@router.post("/{mission_id}/pause", response_model=MissionView, operation_id="pause_mission")
async def pause_mission(
    mission_id: UUID,
    body: RunSelectBody | None = None,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Pause the mission's running run, holding the robot in place.

    Identify the mission by mission_id. Use resume_mission to continue it. With more than one
    run active, ``run_id`` says which.
    """
    try:
        run = await uc.pause(to_pause_command(mission_id, body.run_id if body else None))
    except _STEER_ERRORS as exc:
        raise _steer_errors(exc)
    return await _view(run.mission_id, repo, runs)


@router.post("/{mission_id}/resume", response_model=MissionView, operation_id="resume_mission")
async def resume_mission(
    mission_id: UUID,
    body: RunSelectBody | None = None,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> MissionView:
    """Resume the mission's paused run, letting the robot continue.

    Identify it by mission_id. With more than one run active, ``run_id`` says which.
    """
    try:
        run = await uc.resume(to_resume_command(mission_id, body.run_id if body else None))
    except _STEER_ERRORS as exc:
        raise _steer_errors(exc)
    return await _view(run.mission_id, repo, runs)


@router.post("/{mission_id}/reset", operation_id="reset_mission", include_in_schema=False)
async def reset_mission(mission_id: UUID) -> None:
    """Removed: run a mission again with dispatch_mission; nothing needs resetting."""
    raise HTTPException(
        status.HTTP_410_GONE,
        "reset no longer exists: dispatch the mission again; every run keeps its own record",
    )
