"""EventBus-backed implementation of RobotFactsheetView."""

import structlog
from pydantic import ValidationError

from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView

logger = structlog.get_logger(__name__)


class EventBusBackedRobotFactsheetView(RobotFactsheetView):
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def latest(self, robot_id: str) -> RobotFactsheet | None:
        """Return the robot's latest declaration, or None where it has none this can read.

        A declaration this backend cannot parse costs that robot its capabilities and nothing
        else: raising here would fail the whole fleet view, so one robot ahead of or behind the
        contract would hide every other robot from the operator.
        """
        raw = self._bus.latched(event_topics.robot_topic(robot_id, "factsheet"))
        if not raw:
            return None
        try:
            return RobotFactsheet.model_validate(raw)
        except ValidationError:
            logger.warning("unreadable_factsheet", robot_id=robot_id)
            return None
