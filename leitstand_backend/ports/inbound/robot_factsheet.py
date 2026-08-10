"""Driving port: ingestion of robot-published factsheets."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from leitstand_backend.domain.model.robot.robot_factsheet import RobotFactsheet


class RecordRobotFactsheetCommand(BaseModel):
    factsheet: RobotFactsheet


class RobotFactsheetUseCase(ABC):
    @abstractmethod
    async def record(self, command: RecordRobotFactsheetCommand) -> None: ...
