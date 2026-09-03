"""Mappers: wire DTOs <-> domain commands/views for missions."""

from uuid import UUID

from leitstand_backend.adapters.inbound.web.missions.dto import (
    MissionCoverageCreate,
    MissionCreate,
    MissionUpdate,
    MissionView,
)
from leitstand_backend.ports.inbound.coverage_planning import PlanCoverageCommand
from leitstand_backend.ports.inbound.mission_management import (
    AssignMissionCommand,
    CancelMissionCommand,
    CreateMissionCommand,
    DeleteMissionCommand,
    DispatchMissionCommand,
    PauseMissionCommand,
    ResetMissionCommand,
    ResumeMissionCommand,
    UnassignMissionCommand,
    UpdateMissionCommand,
)
from leitstand_backend.ports.outbound.mission_repository import MissionRecord


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
        replaces=req.replaces,
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


def to_dispatch_command(mission_id: UUID, robot_id: str | None) -> DispatchMissionCommand:
    return DispatchMissionCommand(mission_id=mission_id, robot_id=robot_id)


def to_cancel_command(mission_id: UUID) -> CancelMissionCommand:
    return CancelMissionCommand(mission_id=mission_id)


def to_pause_command(mission_id: UUID) -> PauseMissionCommand:
    return PauseMissionCommand(mission_id=mission_id)


def to_resume_command(mission_id: UUID) -> ResumeMissionCommand:
    return ResumeMissionCommand(mission_id=mission_id)


def to_delete_command(mission_id: UUID) -> DeleteMissionCommand:
    return DeleteMissionCommand(mission_id=mission_id)


def to_reset_command(mission_id: UUID) -> ResetMissionCommand:
    return ResetMissionCommand(mission_id=mission_id)


def to_mission_view(record: MissionRecord) -> MissionView:
    m = record.mission
    return MissionView(
        mission_id=m.mission_id,
        update_id=m.update_id,
        name=m.name,
        description=m.description,
        stages=m.stages,
        status=record.status,
        robot_id=record.robot_id,
        dispatched_at=record.dispatched_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
        failure_errors=record.failure_errors,
        coverage=record.coverage,
    )
