"""Driving port: connectivity events from the field."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot import Metadata, Robot


class RecordOnlineCommand(BaseModel):
    robot_id: str
    metadata: Metadata


class RecordOfflineCommand(BaseModel):
    robot_id: str


class RobotConnectivityUseCase(ABC):
    @abstractmethod
    async def record_online(self, command: RecordOnlineCommand) -> Robot: ...

    @abstractmethod
    async def record_offline(self, command: RecordOfflineCommand) -> Robot | None: ...
