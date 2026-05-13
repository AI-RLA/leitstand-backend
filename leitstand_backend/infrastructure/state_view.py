"""EventBus-backed implementation of RobotStateView."""

from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class EventBusBackedRobotStateView(RobotStateView):
    def __init__(self, bus: EventBus):
        self._bus = bus

    def latest_pose(self, robot_id: str) -> Pose | None:
        raw = self._bus.latched(f"events.robot/{robot_id}/pose")
        return Pose.model_validate(raw) if raw else None

    def latest_battery(self, robot_id: str) -> Battery | None:
        raw = self._bus.latched(f"events.robot/{robot_id}/battery")
        return Battery.model_validate(raw) if raw else None

    def latest_state(self, robot_id: str) -> RobotState | None:
        raw = self._bus.latched(f"events.robot/{robot_id}/state")
        return RobotState.model_validate(raw) if raw else None
