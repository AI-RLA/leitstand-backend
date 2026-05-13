"""Implementation of RobotStateUseCase: operational robot state."""

from leitstand_backend.ports.inbound.robot_state import RecordStateCommand, RobotStateUseCase
from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class RobotStateService(RobotStateUseCase):
    def __init__(self, events: EventPublisher):
        self._events = events

    async def record_state(self, command: RecordStateCommand) -> None:
        topic = f"events.robot/{command.robot_id}/state"
        self._events.publish(topic, command.state.model_dump(mode="json"), latch=True)
        self._events.publish(
            f"events.robot/{command.robot_id}",
            {"kind": "state", "data": command.state.model_dump(mode="json")},
            latch=False,
        )
