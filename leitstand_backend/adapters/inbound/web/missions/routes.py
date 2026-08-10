"""Mission CRUD + lifecycle action endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from leitstand_backend.adapters.inbound.web.missions.dto import (
    MissionAssignBody,
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
    InvalidMissionTransition,
    MissionDispatchTimeout,
    MissionNotFoundError,
    MissionRejectedByRobot,
    NoRobotAssigned,
    RobotBusy,
    RobotFactsheetMissing,
    StageNotHomogeneous,
    UnknownSite,
    UnknownSiteForRobot,
    UnsupportedStageKind,
)
from leitstand_backend.infrastructure.deps import (
    get_dispatch_use_case,
    get_mission_management_use_case,
    get_mission_repository,
)
from leitstand_backend.ports.inbound.mission_management import MissionManagementUseCase
from leitstand_backend.ports.outbound.mission_repository import MissionRepository

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])


@router.get("/", response_model=list[MissionView])
async def list_missions(
    robot: str | None = Query(
        default=None, description="Filter to missions assigned to this robot id."
    ),
    repo: MissionRepository = Depends(get_mission_repository),
) -> list[MissionView]:
    """List missions, newest first, optionally filtered to one robot's missions."""
    return [to_mission_view(r) for r in await repo.list_records(robot_id=robot)]


@router.get("/{mission_id}", response_model=MissionView)
async def get_mission(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    record = await repo.get_record(mission_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    return to_mission_view(record)


@router.get("/{mission_id}/state", response_model=MissionStateView)
async def get_mission_state(
    mission_id: UUID,
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionStateView:
    """Return the mission's per-stage runtime state, with errors attributed per stage."""
    record = await repo.get_record(mission_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    stage_states = await repo.get_stage_states(mission_id)
    return build_mission_state_view(mission_id, stage_states, record.failure_errors)


@router.post("/", response_model=MissionView, status_code=status.HTTP_201_CREATED)
async def create_mission(
    body: MissionCreate,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.create(to_create_command(body))
    except (StageNotHomogeneous, UnknownSite) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.patch("/{mission_id}", response_model=MissionView)
async def update_mission(
    mission_id: UUID,
    body: MissionUpdate,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.update(to_update_command(mission_id, body))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except (StageNotHomogeneous, UnknownSite) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.delete("/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
) -> None:
    try:
        await uc.delete(to_delete_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.post("/{mission_id}/assign", response_model=MissionView)
async def assign_mission(
    mission_id: UUID,
    body: MissionAssignBody,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
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
    except (StageNotHomogeneous, UnsupportedStageKind, UnknownSiteForRobot) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/unassign", response_model=MissionView)
async def unassign_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.unassign(to_unassign_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/dispatch", response_model=MissionView)
async def dispatch_mission(
    mission_id: UUID,
    body: MissionDispatchBody | None = None,
    dispatch_uc=Depends(get_dispatch_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
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
    except (StageNotHomogeneous, UnsupportedStageKind, UnknownSiteForRobot) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionRejectedByRobot as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except MissionDispatchTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/cancel", response_model=MissionView)
async def cancel_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.cancel(to_cancel_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/pause", response_model=MissionView)
async def pause_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.pause(to_pause_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/resume", response_model=MissionView)
async def resume_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.resume(to_resume_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]


@router.post("/{mission_id}/reset", response_model=MissionView)
async def reset_mission(
    mission_id: UUID,
    uc: MissionManagementUseCase = Depends(get_mission_management_use_case),
    repo: MissionRepository = Depends(get_mission_repository),
) -> MissionView:
    try:
        mission = await uc.reset(to_reset_command(mission_id))
    except MissionNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    record = await repo.get_record(mission.mission_id)
    return to_mission_view(record)  # type: ignore[arg-type]
