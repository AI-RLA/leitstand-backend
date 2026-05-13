"""In-memory RobotStateView fake (test-only)."""

from __future__ import annotations

from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class InMemoryRobotStateView(RobotStateView):
    def __init__(self) -> None:
        self._poses: dict[str, Pose] = {}
        self._batteries: dict[str, Battery] = {}
        self._states: dict[str, RobotState] = {}

    def set_pose(self, robot_id: str, pose: Pose) -> None:
        self._poses[robot_id] = pose

    def set_battery(self, robot_id: str, battery: Battery) -> None:
        self._batteries[robot_id] = battery

    def set_state(self, robot_id: str, state: RobotState) -> None:
        self._states[robot_id] = state

    def latest_pose(self, robot_id: str) -> Pose | None:
        return self._poses.get(robot_id)

    def latest_battery(self, robot_id: str) -> Battery | None:
        return self._batteries.get(robot_id)

    def latest_state(self, robot_id: str) -> RobotState | None:
        return self._states.get(robot_id)
