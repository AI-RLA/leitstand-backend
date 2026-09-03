"""RobotFactsheetService - persist the latest factsheet per robot and latch it on the EventBus."""

from leitstand_backend.domain import event_topics
from leitstand_backend.ports.inbound.robot_factsheet import (
    RecordRobotFactsheetCommand,
    RobotFactsheetUseCase,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.robot_repository import RobotRepository


class RobotFactsheetService(RobotFactsheetUseCase):
    def __init__(self, repo: RobotRepository, events: EventPublisher):
        self._repo = repo
        self._events = events

    async def record(self, command: RecordRobotFactsheetCommand) -> None:
        """Persist the declaration, then latch it for readers.

        Persisting first keeps the latch from ever being newer than the row: a crash between the
        two leaves a durable factsheet that the next startup re-latches, whereas the reverse order
        would leave readers holding a capability claim no restart could recover.
        """
        robot_id = command.factsheet.robot_id
        payload = command.factsheet.model_dump(mode="json")
        await self._repo.save_factsheet(robot_id, payload)
        self._events.publish(event_topics.robot_topic(robot_id, "factsheet"), payload, latch=True)
