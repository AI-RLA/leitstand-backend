"""RobotFactsheetView outbound port: latest factsheet per robot."""

from abc import ABC, abstractmethod

from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet


class RobotFactsheetView(ABC):
    """Latest known factsheet per robot.

    Backed by an in-process latched cache populated by the inbound
    factsheet adapter. Read by application services at dispatch time
    for pre-flight validation.
    """

    @abstractmethod
    def latest(self, robot_id: str) -> RobotFactsheet | None: ...
