"""EventBus-backed implementation of RobotStateView."""

from leitstand_backend.domain.model.robot.telemetry import Battery, Pose
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.outbound.event_publisher import robot_topic
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView


class EventBusBackedRobotStateView(RobotStateView):
    def __init__(self, bus: EventBus):
        self._bus = bus

    def latest_pose(self, robot_id: str) -> Pose | None:
        raw = self._bus.latched(robot_topic(robot_id, "pose"))
        return Pose.model_validate(raw) if raw else None

    def latest_battery(self, robot_id: str) -> Battery | None:
        raw = self._bus.latched(robot_topic(robot_id, "battery"))
        return Battery.model_validate(raw) if raw else None
