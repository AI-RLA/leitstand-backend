"""Driving port: connectivity events from the field."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot.robot import Metadata, Robot


class RecordOnlineCommand(BaseModel):
    robot_id: str
    metadata: Metadata
    # The run the robot says it is executing, or None; ``claim_reported`` is False for a client
    # that does not answer the field at all, which must not read as "no run".
    active_run_id: str | None = None
    claim_reported: bool = False


class RecordOfflineCommand(BaseModel):
    robot_id: str


class RobotConnectivityUseCase(ABC):
    @abstractmethod
    async def record_online(self, command: RecordOnlineCommand) -> Robot: ...

    @abstractmethod
    async def record_offline(self, command: RecordOfflineCommand) -> Robot | None: ...
