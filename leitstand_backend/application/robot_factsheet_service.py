"""RobotFactsheetService - latch the latest factsheet per robot via the EventBus."""

from leitstand_backend.domain import event_topics
from leitstand_backend.ports.inbound.robot_factsheet import (
    RecordRobotFactsheetCommand,
    RobotFactsheetUseCase,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class RobotFactsheetService(RobotFactsheetUseCase):
    def __init__(self, events: EventPublisher):
        self._events = events

    async def record(self, command: RecordRobotFactsheetCommand) -> None:
        topic = event_topics.robot_topic(command.factsheet.robot_id, "factsheet")
        self._events.publish(topic, command.factsheet.model_dump(mode="json"), latch=True)
