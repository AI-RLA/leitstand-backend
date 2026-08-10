"""Driving port: ingestion of robot-published mission state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.mission.mission_state import MissionStateMessage


class RecordMissionStateCommand(BaseModel):
    robot_id: str = PField(min_length=1)
    state: MissionStateMessage


class HandleRobotOfflineCommand(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionStateUseCase(ABC):
    @abstractmethod
    async def record(self, command: RecordMissionStateCommand) -> None: ...

    @abstractmethod
    async def handle_robot_offline(self, command: HandleRobotOfflineCommand) -> None: ...
