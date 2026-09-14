"""FleetViewService: compose Robot + latest telemetry + derived operational status."""

from leitstand_backend.domain.model.mission.mission_run import MissionRunSummary
from leitstand_backend.domain.model.mission.run_lifecycle import is_executing
from leitstand_backend.domain.model.robot.robot import Robot
from leitstand_backend.domain.model.robot.robot_status import derive_robot_status
from leitstand_backend.ports.inbound.fleet_view import FleetViewUseCase, RobotOverview
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class FleetViewService(FleetViewUseCase):
    def __init__(
        self,
        repo: RobotRepository,
        state_view: RobotStateView,
        runs: MissionRunRepository,
        factsheets: RobotFactsheetView,
    ):
        self._repo = repo
        self._state_view = state_view
        self._runs = runs
        self._factsheets = factsheets

    async def get_robot(self, robot_id: str) -> RobotOverview | None:
        robot = await self._repo.get(robot_id)
        if robot is None:
            return None
        return self._build_overview(robot, await self._current_runs())

    async def list_robots(self) -> list[RobotOverview]:
        robots = await self._repo.list()
        current = await self._current_runs()
        return [self._build_overview(r, current) for r in robots]

    async def _current_runs(self) -> dict[str, MissionRunSummary]:
        """Every robot's active run in one query; a robot holds at most one."""
        return {run.robot_id: run for run in await self._runs.list_active()}

    def _build_overview(
        self, robot: Robot, current_runs: dict[str, MissionRunSummary]
    ) -> RobotOverview:
        battery = self._state_view.latest_battery(robot.id)
        current = current_runs.get(robot.id)
        status = derive_robot_status(
            online=robot.online,
            has_active_mission=current is not None and is_executing(current.status),
            charging=bool(battery and battery.charging),
        )
        return RobotOverview(
            robot=robot,
            pose=self._state_view.latest_pose(robot.id),
            battery=battery,
            status=status,
            factsheet=self._factsheets.latest(robot.id),
            current_run=current,
        )
