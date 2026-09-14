"""Robot status service: compute a robot's derived operational status.

``compute`` reads the backend-owned facts for one robot (liveliness, whether it has an
executing run, battery) and applies ``derive_robot_status``. It returns only the operational
label; which mission a robot runs is the mission resource's concern.
"""

from leitstand_backend.domain.model.robot.robot_status import RobotStatus, derive_robot_status
from leitstand_backend.ports.outbound.mission_run_repository import MissionRunRepository
from leitstand_backend.ports.outbound.robot_repository import RobotRepository
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class RobotStatusService:
    """Compute one robot's derived operational status from backend-owned facts.

    Wired with session-bound repositories per call (see the projector and fleet view),
    matching the other per-call service wiring.
    """

    def __init__(
        self,
        robots: RobotRepository,
        runs: MissionRunRepository,
        state_view: RobotStateView,
    ) -> None:
        self._robots = robots
        self._runs = runs
        self._state_view = state_view

    async def compute(self, robot_id: str) -> RobotStatus | None:
        """Return the robot's derived status, or None when the robot is unknown."""
        robot = await self._robots.get(robot_id)
        if robot is None:
            return None
        has_active = await self._runs.has_executing_run(robot_id)
        battery = self._state_view.latest_battery(robot_id)
        return derive_robot_status(
            online=robot.online,
            has_active_mission=has_active,
            charging=bool(battery and battery.charging),
        )
