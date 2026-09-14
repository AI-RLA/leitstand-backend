"""Run endpoints: history reads, annotation, and removal of finished runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from leitstand_backend.adapters.inbound.web.runs.dto import RunNotesPatch, RunSummaryView, RunView
from leitstand_backend.adapters.inbound.web.runs.mappers import to_run_summary_view, to_run_view
from leitstand_backend.application.run_state_view import RunStateView, build_run_state_view
from leitstand_backend.domain.errors import InvalidMissionTransition, RunNotFoundError
from leitstand_backend.domain.model.mission.stage_state_record import waiting_stage_statuses
from leitstand_backend.infrastructure.deps import (
    get_mission_repository,
    get_mission_run_repository,
    get_run_management_use_case,
)
from leitstand_backend.ports.inbound.run_management import (
    AnnotateRunCommand,
    DeleteRunCommand,
    RunManagementUseCase,
)
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository

router = APIRouter(prefix="/api/v1", tags=["runs"])


@router.get(
    "/missions/{mission_id}/runs",
    response_model=list[RunSummaryView],
    operation_id="list_mission_runs",
)
async def list_mission_runs(
    mission_id: UUID,
    limit: int = Query(default=50, ge=1, le=500),
    before: datetime | None = Query(
        default=None, description="Only runs created before this instant; pages backwards."
    ),
    missions: MissionRepository = Depends(get_mission_repository),
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> list[RunSummaryView]:
    """List a mission's runs, newest first, without their plans.

    Every execution the mission has ever had, whatever its outcome. Each carries its own robot,
    timings, status and a digest of the plan it executed, so runs of an edited mission can be
    told apart from runs of the plan as it stands.
    """
    if await missions.get(mission_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mission not found")
    return [
        to_run_summary_view(r)
        for r in await runs.list_by_mission(mission_id, limit=limit, before=before)
    ]


@router.get("/runs/{run_id}", response_model=RunView, operation_id="get_run")
async def get_run(
    run_id: UUID,
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> RunView:
    """Return one run with the plan it executed, frozen at dispatch, and its failure cause."""
    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return to_run_view(run)


@router.get("/runs/{run_id}/state", response_model=RunStateView, operation_id="get_run_state")
async def get_run_state(
    run_id: UUID,
    runs: MissionRunRepository = Depends(get_mission_run_repository),
) -> RunStateView:
    """Return one run's per-stage state with errors attributed per stage.

    A run the robot has not reported on yet projects every stage as WAITING from its own plan.
    """
    run = await runs.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    rows = await runs.get_stage_runs(run_id) or waiting_stage_statuses(run.stages, run.created_at)
    return build_run_state_view(run.run_id, run.mission_id, rows, run.failure_errors)


@router.patch("/runs/{run_id}", response_model=RunView, operation_id="annotate_run")
async def annotate_run(
    run_id: UUID,
    body: RunNotesPatch,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
) -> RunView:
    """Set or clear a run's notes. Notes are the operator's, editable at any time."""
    try:
        run = await uc.annotate(AnnotateRunCommand(run_id=run_id, notes=body.notes))
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return to_run_view(run)


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="delete_run")
async def delete_run(
    run_id: UUID,
    uc: RunManagementUseCase = Depends(get_run_management_use_case),
) -> None:
    """Remove a finished run and its per-stage record.

    A run still active is refused (409): its robot is driving it. The audit log keeps every
    completed delete; what goes is the detail.
    """
    try:
        await uc.delete(DeleteRunCommand(run_id=run_id))
    except RunNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    except InvalidMissionTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
