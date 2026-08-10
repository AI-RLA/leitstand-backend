"""Driving port: ingest sensor telemetry (pose + battery)."""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot.telemetry import Battery, Pose


class RecordPoseCommand(BaseModel):
    robot_id: str
    pose: Pose


class RecordBatteryCommand(BaseModel):
    robot_id: str
    battery: Battery


class RobotTelemetryUseCase(ABC):
    @abstractmethod
    async def record_pose(self, command: RecordPoseCommand) -> None: ...

    @abstractmethod
    async def record_battery(self, command: RecordBatteryCommand) -> None: ...
