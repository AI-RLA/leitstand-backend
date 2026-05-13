"""FleetViewService — composes Robot + latest telemetry."""

from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase, RobotOverview
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class FleetViewService(FleetViewUseCase):
    def __init__(self, repo: RobotRepository, state_view: RobotStateView):
        self._repo = repo
        self._state_view = state_view

    async def get_robot(self, robot_id: str) -> RobotOverview | None:
        robot = await self._repo.get(robot_id)
        if robot is None:
            return None
        return self._build_overview(robot)

    async def list_robots(self) -> list[RobotOverview]:
        robots = await self._repo.list()
        return [self._build_overview(r) for r in robots]

    def _build_overview(self, robot) -> RobotOverview:
        return RobotOverview(
            robot=robot,
            pose=self._state_view.latest_pose(robot.id),
            battery=self._state_view.latest_battery(robot.id),
            state=self._state_view.latest_state(robot.id),
        )
