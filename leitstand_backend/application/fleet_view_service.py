"""FleetViewService: compose Robot + latest telemetry + derived operational status."""

from leitstand_backend.domain.model.mission.mission_lifecycle import is_executing
from leitstand_backend.domain.model.robot.robot import Robot
from leitstand_backend.domain.model.robot.robot_status import derive_robot_status
from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase, RobotOverview
from leitstand_backend.ports.outbound.mission_repository import MissionRepository
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class FleetViewService(FleetViewUseCase):
    def __init__(
        self,
        repo: RobotRepository,
        state_view: RobotStateView,
        missions: MissionRepository,
    ):
        self._repo = repo
        self._state_view = state_view
        self._missions = missions

    async def get_robot(self, robot_id: str) -> RobotOverview | None:
        robot = await self._repo.get(robot_id)
        if robot is None:
            return None
        return self._build_overview(robot, await self._executing_robot_ids())

    async def list_robots(self) -> list[RobotOverview]:
        robots = await self._repo.list()
        executing = await self._executing_robot_ids()
        return [self._build_overview(r, executing) for r in robots]

    async def _executing_robot_ids(self) -> set[str]:
        """Return the ids of robots that currently have an executing mission."""
        records = await self._missions.list_records()
        return {r.robot_id for r in records if r.robot_id and is_executing(r.status)}

    def _build_overview(self, robot: Robot, executing_robot_ids: set[str]) -> RobotOverview:
        battery = self._state_view.latest_battery(robot.id)
        status = derive_robot_status(
            online=robot.online,
            has_active_mission=robot.id in executing_robot_ids,
            charging=bool(battery and battery.charging),
        )
        return RobotOverview(
            robot=robot,
            pose=self._state_view.latest_pose(robot.id),
            battery=battery,
            status=status,
        )
