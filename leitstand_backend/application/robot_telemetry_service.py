"""Implementation of RobotTelemetryUseCase: sensor telemetry (pose + battery)."""

from leitstand_backend.ports.inbound.robot_telemetry import (
    RecordBatteryCommand,
    RecordPoseCommand,
    RobotTelemetryUseCase,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class RobotTelemetryService(RobotTelemetryUseCase):
    def __init__(self, events: EventPublisher):
        self._events = events

    async def record_pose(self, command: RecordPoseCommand) -> None:
        topic = f"events.robot/{command.robot_id}/pose"
        self._events.publish(topic, command.pose.model_dump(mode="json"), latch=True)
        self._events.publish(
            f"events.robot/{command.robot_id}",
            {"kind": "pose", "data": command.pose.model_dump(mode="json")},
            latch=False,
        )

    async def record_battery(self, command: RecordBatteryCommand) -> None:
        topic = f"events.robot/{command.robot_id}/battery"
        self._events.publish(topic, command.battery.model_dump(mode="json"), latch=True)
        self._events.publish(
            f"events.robot/{command.robot_id}",
            {"kind": "battery", "data": command.battery.model_dump(mode="json")},
            latch=False,
        )
