"""Driving port: operator views of the fleet."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot import Robot
from leitstand_backend.domain.model.telemetry import Battery, Pose, RobotState


class RobotOverview(BaseModel):
    """Output contract for FleetViewUseCase queries.

    Composes a Robot with the latest cached telemetry triple. Transient,
    in-process; never persisted. Per ADR 0018's port-file pattern (input
    Commands colocated with the use case ABC), output contracts live
    here too.
    """

    robot: Robot
    pose: Pose | None = None
    battery: Battery | None = None
    state: RobotState | None = None


class FleetViewUseCase(ABC):
    @abstractmethod
    async def get_robot(self, robot_id: str) -> RobotOverview | None: ...

    @abstractmethod
    async def list_robots(self) -> list[RobotOverview]: ...
