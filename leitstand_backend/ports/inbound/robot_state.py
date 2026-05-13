"""Driving port: ingest robot operational state."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.telemetry import RobotState


class RecordStateCommand(BaseModel):
    robot_id: str
    state: RobotState


class RobotStateUseCase(ABC):
    @abstractmethod
    async def record_state(self, command: RecordStateCommand) -> None: ...
