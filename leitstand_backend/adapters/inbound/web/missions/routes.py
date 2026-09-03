"""Mission CRUD + lifecycle action endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from leitstand_backend.adapters.inbound.web.missions.dto import (
    MissionAssignBody,
    MissionCoverageCreate,
    MissionCreate,
    MissionDispatchBody,
    MissionUpdate,
    MissionView,
)
from leitstand_backend.adapters.inbound.web.missions.mappers import (
    to_assign_command,
    to_cancel_command,
    to_create_command,
    to_delete_command,
    to_dispatch_command,
    to_mission_view,
    to_pause_command,
    to_plan_coverage_command,
    to_reset_command,
    to_resume_command,
    to_unassign_command,
    to_update_command,
)
from leitstand_backend.application.mission_state_view import (
    MissionStateView,
    build_mission_state_view,
)
from leitstand_backend.domain.errors import (
    CoveragePlannerUnavailable,
    CoveragePlanRejected,
    FieldNotFoundError,
    FieldNotPlannable,
    GeneratedPlanNotEditable,
    ImplementNarrowerThanRobot,
    IncompatibleTurningRadius,
    InvalidMissionTransition,
    MissionDispatchTimeout,
    MissionNotFoundError,
    MissionRejectedByRobot,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    RobotPhysicalParametersMissing,
    StageNotHomogeneous,
    StaleCoverageBoundary,
    UnknownSite,
    UnknownSiteForRobot,
    UnsupportedStageKind,
    UnsupportedWaypointFrame,
)
from leitstand_backend.infrastructure.deps import (
    get_coverage_planning_use_case,
    get_dispatch_use_case,
    get_mission_management_use_case,
    get_mission_repository,
)
from leitstand_backend.ports.inbound.coverage_planning import CoveragePlanningUseCase
from leitstand_backend.ports.inbound.mission_management import MissionManagementUseCase
from leitstand_backend.ports.outbound.mission_repository import MissionRepository

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])


@router.get("/", response_model=list[MissionView], operation_id="list_missions")
async def list_missions(
    robot: str | None = Query(
        default=None, description="Filter to missions assigned to this robot id."
    ),
    name: str | None = Query(
        default=None,
        description="Filter to missions with exactly this name, matched case-insensitively.",
    ),
    repo: MissionRepository = Depends(get_mission_repository),
) -> list[MissionView]:
    """List missions newest first, each with its lifecycle status and assigned robot.

    Status is one of DRAFT, ASSIGNED, DISPATCHED, RUNNING, PAUSED, SUCCEEDED, FAILED or
    CANCELLED. Optionally filter by robot, by name, or both. This returns mission definitions and
    status, not live per-stage progress: use get_mission_state for that.
    """
    return [to_mission_view(r) for r in await repo.list_records(robot_id=robot, name=name)]


@router.get("/{mission_id}", response_model=MissionView, operation_id="get_mission")
async def get_mission(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Return one mission's definition, lifecycle status and assigned robot.

    Identify the mission by mission_id from list_missions, which is also how a mission named by
    the operator is resolved.
    """
    record = await repo.get_record(mission_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    return to_mission_view(record)


@router.get(
    "/{mission_id}/state", response_model=MissionStateView, operation_id="get_mission_state"
)
async def get_mission_state(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionStateView:
    """Return live per-stage progress for one mission, with errors attributed per stage.

    Each stage carries a status and a progress fraction. Use this to answer how far along a
    mission is, or why it failed. Identify the mission by mission_id from list_missions.
    """
    record = await repo.get_record(mission_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    stage_states = await repo.get_stage_states(mission_id)
    return build_mission_state_view(mission_id, stage_states, record.failure_errors)


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
) -> MissionView:
    """Create a mission from a name and an ordered list of navigation stages.

    Every waypoint must be a coordinate the operator stated. Do not compute one: not from a field
    or site boundary, not from a robot's current position, and not by converting a distance in
    metres into degrees. If you were given an area, a row spacing or a bearing rather than
    coordinates, do not call this: use plan_coverage_mission for a field that should be covered, and
    otherwise say you cannot work the coordinates out and ask for them.

    ``stages`` is a list of stage objects, not text containing a list.

    The backend assigns the mission and stage ids. The mission is created as a draft and is not
    dispatched.
    """
    try:
        mission = await uc.create(to_create_command(body))
    except (StageNotHomogeneous, UnknownSite) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


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
) -> MissionView:
    """Create a mission that covers a whole field, planned from the field's own boundary.

    Use this whenever the operator asks to cover, survey, mow or treat a field rather than to
    drive to stated points. Give the field_id, the robot the plan is for, and the working width
    in metres; supply no coordinates, because the path is computed from the stored boundary, and
    no turning radius, because the robot declares its own.

    The mission is created as a draft and is not dispatched. Its coverage metrics come back with
    it, so the operator can judge the plan before dispatching it.
    """
    try:
        mission = await uc.plan(to_plan_coverage_command(body))
    except FieldNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission to replace not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (
        CoveragePlanRejected,
        FieldNotPlannable,
        RobotFactsheetMissing,
        RobotPhysicalParametersMissing,
        StageNotHomogeneous,
        UnsupportedStageKind,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except CoveragePlannerUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.patch("/{mission_id}", response_model=MissionView, operation_id="update_mission")
async def update_mission(
    mission_id: UUID,
    body: MissionUpdate,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Change a draft mission's name, description, or stages.

    Only a mission still in DRAFT can be updated. Identify it by mission_id from list_missions.
    Supplying stages replaces the existing ones, which a planned mission refuses: re-plan it.
    """
    try:
        mission = await uc.update(to_update_command(mission_id, body))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (StageNotHomogeneous, UnknownSite, GeneratedPlanNotEditable) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.delete(
    "/{mission_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_mission"
)
async def delete_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
) -> None:
    """Delete a mission permanently.

    Only a draft or already-finished mission can be deleted, and this cannot be undone. Identify
    the mission by mission_id from list_missions.
    """
    try:
        await uc.delete(to_delete_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/{mission_id}/assign", response_model=MissionView, operation_id="assign_mission")
async def assign_mission(
    mission_id: UUID,
    body: MissionAssignBody,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Assign a mission to a robot, readying it for dispatch without starting it.

    Identify the mission by mission_id and give the robot_id. To also start it, use
    dispatch_mission instead.
    """
    try:
        mission = await uc.assign(to_assign_command(mission_id, body.robot_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except RobotBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except RobotFactsheetMissing as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except (
        StageNotHomogeneous,
        UnsupportedStageKind,
        UnknownSiteForRobot,
        UnsupportedWaypointFrame,
        IncompatibleTurningRadius,
        ImplementNarrowerThanRobot,
        RobotPhysicalParametersMissing,
        StaleCoverageBoundary,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/unassign", response_model=MissionView, operation_id="unassign_mission")
async def unassign_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Remove the robot assignment from a mission that has not yet been dispatched.

    Identify the mission by mission_id.
    """
    try:
        mission = await uc.unassign(to_unassign_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/dispatch", response_model=MissionView, operation_id="dispatch_mission")
async def dispatch_mission(
    mission_id: UUID,
    body: MissionDispatchBody | None = None,
    dispatch_uc=Depends(get_dispatch_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Dispatch a mission to a robot and start it driving.

    Sends the mission to its assigned robot, or to ``robot_id`` when given, and begins execution.
    The mission must be in DRAFT or ASSIGNED; identify it by ``mission_id`` from list_missions.
    """
    try:
        mission = await dispatch_uc.dispatch(
            to_dispatch_command(mission_id, body.robot_id if body else None)
        )
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except RobotBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except NoRobotAssigned as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except RobotFactsheetMissing as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except (
        StageNotHomogeneous,
        UnsupportedStageKind,
        UnknownSiteForRobot,
        UnsupportedWaypointFrame,
        IncompatibleTurningRadius,
        ImplementNarrowerThanRobot,
        RobotPhysicalParametersMissing,
        StaleCoverageBoundary,
    ) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionRejectedByRobot as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionDispatchTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/cancel", response_model=MissionView, operation_id="cancel_mission")
async def cancel_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Cancel a mission, stopping the robot if it is running.

    Identify it by mission_id from list_missions.
    """
    try:
        mission = await uc.cancel(to_cancel_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/pause", response_model=MissionView, operation_id="pause_mission")
async def pause_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Pause a running mission, holding the robot in place.

    Identify the mission by mission_id. Use resume_mission to continue it.
    """
    try:
        mission = await uc.pause(to_pause_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/resume", response_model=MissionView, operation_id="resume_mission")
async def resume_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Resume a paused mission, letting the robot continue.

    Identify it by mission_id.
    """
    try:
        mission = await uc.resume(to_resume_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/reset", response_model=MissionView, operation_id="reset_mission")
async def reset_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    """Return a failed or cancelled mission to draft so it can be edited and dispatched again.

    Identify the mission by mission_id.
    """
    try:
        mission = await uc.reset(to_reset_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]
