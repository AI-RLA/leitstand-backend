"""Driving port: operator views of the fleet."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot.robot import Robot
from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.domain.model.robot.telemetry import Battery, Pose


class RobotOverview(BaseModel):
    """Output contract for FleetViewUseCase queries.

    Composes a Robot with its latest cached telemetry, its derived operational ``status``, and
    what it has declared it can do. Transient, in-process; never persisted.
    """

    robot: Robot
    pose: Pose | None = None
    battery: Battery | None = None
    status: RobotStatus
    # Shown because the backend refuses dispatch on it, and an unexplained refusal reads as a fault.
    factsheet: RobotFactsheet | None = None


class FleetViewUseCase(ABC):
    @abstractmethod
    async def get_robot(self, robot_id: str) -> RobotOverview | None: ...

    @abstractmethod
    async def list_robots(self) -> list[RobotOverview]: ...
