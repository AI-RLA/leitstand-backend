"""In-memory RobotFactsheetView fake (test-only)."""

from __future__ import annotations

import threading

from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet
from leitstand_backend.ports.outbound.robot_factsheet_view import RobotFactsheetView


class InMemoryRobotFactsheetView(RobotFactsheetView):
    def __init__(self) -> None:
        self._factsheets: dict[str, RobotFactsheet] = {}
        self._lock = threading.Lock()

    def latest(self, robot_id: str) -> RobotFactsheet | None:
        with self._lock:
            return self._factsheets.get(robot_id)

    # Test-only inspection / population helpers -----------------------------------

    def set(self, factsheet: RobotFactsheet) -> None:
        with self._lock:
            self._factsheets[factsheet.robot_id] = factsheet

    def clear(self, robot_id: str) -> None:
        with self._lock:
            self._factsheets.pop(robot_id, None)
