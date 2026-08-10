"""EventBus-backed implementation of RobotFactsheetView."""

from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView


class EventBusBackedRobotFactsheetView(RobotFactsheetView):
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def latest(self, robot_id: str) -> RobotFactsheet | None:
        raw = self._bus.latched(event_topics.robot_topic(robot_id, "factsheet"))
        return RobotFactsheet.model_validate(raw) if raw else None
