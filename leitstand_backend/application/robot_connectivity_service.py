"""Implementation of RobotConnectivityUseCase."""

import structlog

from leitstand_backend.domain.model.robot import Robot
from leitstand_backend.ports.inbound.robot_connectivity import (
    RecordOfflineCommand,
    RecordOnlineCommand,
    RobotConnectivityUseCase,
)
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.robot_repository import RobotRepository

logger = structlog.get_logger(__name__)


class RobotConnectivityService(RobotConnectivityUseCase):
    def __init__(self, repo: RobotRepository, events: EventPublisher):
        self._repo = repo
        self._events = events

    async def record_online(self, command: RecordOnlineCommand) -> Robot:
        robot = await self._repo.record_online(command.robot_id, command.metadata)
        self._events.publish(
            "events.registry",
            {"type": "robot.online", "robot_id": command.robot_id},
            latch=False,
        )
        return robot

    async def record_offline(self, command: RecordOfflineCommand) -> Robot | None:
        robot = await self._repo.record_offline(command.robot_id)
        if robot is not None:
            self._events.publish(
                "events.registry",
                {"type": "robot.offline", "robot_id": command.robot_id},
                latch=False,
            )
        return robot
