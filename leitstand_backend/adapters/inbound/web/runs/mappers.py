"""Mappers: domain runs -> wire views."""

from leitstand_backend.adapters.inbound.web.runs.dto import (
    LastReportView,
    RunOriginView,
    RunSummaryView,
    RunTransitionView,
    RunView,
)
from leitstand_backend.domain.model.mission.mission_run import MissionRun, MissionRunSummary


def to_run_summary_view(run: MissionRunSummary) -> RunSummaryView:
    return RunSummaryView(
        run_id=run.run_id,
        mission_id=run.mission_id,
        robot_id=run.robot_id,
        status=run.status,
        stages_digest=run.stages_digest,
        origin=RunOriginView(
            kind=run.origin.kind, actor=run.origin.actor, tool_call_id=run.origin.tool_call_id
        ),
        notes=run.notes,
        created_at=run.created_at,
        dispatched_at=run.dispatched_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        last_report=(
            LastReportView(
                received_at=run.last_report.received_at,
                header_id=run.last_report.header_id,
                exec_status=run.last_report.exec_status,
                robot_timestamp=run.last_report.robot_timestamp,
            )
            if run.last_report
            else None
        ),
        updated_at=run.updated_at,
    )


def to_run_view(run: MissionRun) -> RunView:
    return RunView(
        **to_run_summary_view(run).model_dump(),
        stages=run.stages,
        site_anchors=run.site_anchors,
        failure_errors=run.failure_errors,
        transitions=[
            RunTransitionView(
                from_status=t.from_status,
                to_status=t.to_status,
                trigger=t.trigger,
                at=t.at,
                actor=t.actor,
                acknowledged=t.acknowledged,
                detail=t.detail,
            )
            for t in run.transitions
        ],
    )
