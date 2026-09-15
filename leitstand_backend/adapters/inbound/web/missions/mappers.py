"""Mappers: wire DTOs <-> domain commands/views for missions."""

from collections.abc import Sequence
from uuid import UUID

from leitstand_backend.adapters.inbound.web.missions.dto import (
    CancelBody,
    MissionCoverageCreate,
    MissionCreate,
    MissionDispatchBody,
    MissionUpdate,
    MissionView,
)
from leitstand_backend.adapters.inbound.web.runs.mappers import to_run_summary_view
from leitstand_backend.domain.model.mission.mission import Mission
from leitstand_backend.domain.model.mission.mission_run import MissionRunSummary, RunOrigin
from leitstand_backend.domain.model.mission.stages_digest import stages_digest
from leitstand_backend.ports.inbound.coverage_planning import PlanCoverageCommand
from leitstand_backend.ports.inbound.mission_management import (
    AssignMissionCommand,
    CreateMissionCommand,
    DeleteMissionCommand,
    RestoreMissionCommand,
    UnassignMissionCommand,
    UpdateMissionCommand,
)
from leitstand_backend.ports.inbound.run_management import (
    CancelRunCommand,
    CloseRunCommand,
    PauseRunCommand,
    ResumeRunCommand,
    StartRunCommand,
)


def to_create_command(req: MissionCreate) -> CreateMissionCommand:
    return CreateMissionCommand(name=req.name, description=req.description, stages=req.stages)


def to_plan_coverage_command(req: MissionCoverageCreate) -> PlanCoverageCommand:
    return PlanCoverageCommand(
        field_id=req.field_id,
        robot_id=req.robot_id,
        name=req.name,
        description=req.description,
        operation_width_m=req.operation_width_m,
        headland_width_m=req.headland_width_m,
        swath_angle_deg=req.swath_angle_deg,
        allow_overlap=req.allow_overlap,
        replan=req.replan,
    )


def to_update_command(mission_id: UUID, req: MissionUpdate) -> UpdateMissionCommand:
    return UpdateMissionCommand(
        mission_id=mission_id,
        name=req.name,
        description=req.description,
        stages=req.stages,
    )


def to_assign_command(mission_id: UUID, robot_id: str) -> AssignMissionCommand:
    return AssignMissionCommand(mission_id=mission_id, robot_id=robot_id)


def to_unassign_command(mission_id: UUID) -> UnassignMissionCommand:
    return UnassignMissionCommand(mission_id=mission_id)


def to_start_run_command(
    mission_id: UUID, body: MissionDispatchBody | None, origin: RunOrigin
) -> StartRunCommand:
    return StartRunCommand(
        mission_id=mission_id,
        robot_id=body.robot_id if body else None,
        notes=body.notes if body else None,
        origin=origin,
    )


def to_cancel_command(mission_id: UUID, body: CancelBody | None) -> CancelRunCommand:
    if body is None:
        return CancelRunCommand(mission_id=mission_id)
    return CancelRunCommand(mission_id=mission_id, run_id=body.run_id, mode=body.mode)


def to_close_command(mission_id: UUID, run_id: UUID | None) -> CloseRunCommand:
    return CloseRunCommand(mission_id=mission_id, run_id=run_id)


def to_pause_command(mission_id: UUID, run_id: UUID | None) -> PauseRunCommand:
    return PauseRunCommand(mission_id=mission_id, run_id=run_id)


def to_resume_command(mission_id: UUID, run_id: UUID | None) -> ResumeRunCommand:
    return ResumeRunCommand(mission_id=mission_id, run_id=run_id)


def to_delete_command(mission_id: UUID) -> DeleteMissionCommand:
    return DeleteMissionCommand(mission_id=mission_id)


def to_restore_command(mission_id: UUID) -> RestoreMissionCommand:
    return RestoreMissionCommand(mission_id=mission_id)


def to_mission_view(
    m: Mission,
    latest_run: MissionRunSummary | None,
    active_runs: Sequence[MissionRunSummary] = (),
) -> MissionView:
    return MissionView(
        mission_id=m.mission_id,
        name=m.name,
        description=m.description,
        stages=m.stages,
        assigned_robot_id=m.assigned_robot_id,
        archived_at=m.archived_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
        stages_digest=stages_digest(m.stages),
        latest_run=to_run_summary_view(latest_run) if latest_run else None,
        active_runs=[to_run_summary_view(r) for r in active_runs],
    )
