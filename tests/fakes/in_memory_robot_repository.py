"""In-memory RobotRepository fake (test-only)."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from leitstand_backend.domain.model.robot.robot import Metadata, Robot
from leitstand_backend.ports.outbound.robot_repository import RobotRepository

logger = logging.getLogger(__name__)


class InMemoryRobotRepository(RobotRepository):
    def __init__(self) -> None:
        self._robots: dict[str, Robot] = {}
        # Stored rather than discarded like telemetry, so tests can assert what was persisted.
        self.factsheets: dict[str, dict] = {}
        self._lock = threading.Lock()

    async def record_online(self, robot_id: str, metadata: Metadata) -> Robot:
        with self._lock:
            previous = self._robots.get(robot_id)
            if previous is not None and previous.online:
                logger.warning("[registry] conflict: robot id %r already online", robot_id)
            robot = Robot(
                id=robot_id,
                metadata=metadata,
                online=True,
                last_seen=datetime.now(timezone.utc),
            )
            self._robots[robot_id] = robot
            logger.info("[registry] online: %s", robot_id)
        return robot

    async def record_offline(self, robot_id: str) -> Robot | None:
        with self._lock:
            robot = self._robots.get(robot_id)
            if robot is None:
                return None
            if not robot.online:
                return robot
            updated = robot.model_copy(update={"online": False})
            self._robots[robot_id] = updated
            logger.info("[registry] offline: %s", robot_id)
            return updated

    async def get(self, robot_id: str) -> Robot | None:
        with self._lock:
            return self._robots.get(robot_id)

    async def list(self) -> list[Robot]:
        with self._lock:
            return sorted(self._robots.values(), key=lambda r: r.id)

    async def mark_all_offline(self) -> None:
        with self._lock:
            for rid, robot in list(self._robots.items()):
                if robot.online:
                    self._robots[rid] = robot.model_copy(update={"online": False})

    async def save_telemetry(self, robot_id: str, kind: str, payload: dict) -> None:
        pass

    async def save_factsheet(self, robot_id: str, payload: dict) -> None:
        with self._lock:
            self.factsheets[robot_id] = payload
