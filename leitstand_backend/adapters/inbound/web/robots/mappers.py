"""Mappers: RobotOverview (port output contract) -> RobotView (wire)."""

from leitstand_backend.adapters.inbound.web.robots.dto import RobotView
from leitstand_backend.adapters.inbound.web.runs.mappers import to_run_summary_view
from leitstand_backend.ports.inbound.fleet_view import RobotOverview


def to_robot_view(overview: RobotOverview) -> RobotView:
    return RobotView(
        id=overview.robot.id,
        metadata=overview.robot.metadata,
        online=overview.robot.online,
        last_seen=overview.robot.last_seen,
        pose=overview.pose,
        battery=overview.battery,
        status=overview.status,
        factsheet=overview.factsheet,
        current_run=(to_run_summary_view(overview.current_run) if overview.current_run else None),
    )
