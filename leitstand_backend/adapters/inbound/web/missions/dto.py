"""Wire DTOs for /api/v1/missions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from pydantic import Field as PField

from leitstand_backend.domain.model.mission.mission import MissionStatus, Stage
from leitstand_backend.domain.model.mission.mission_state import MissionError
from leitstand_backend.ports.inbound.mission_management import StageInput


class MissionCreate(BaseModel):
    name: str = PField(min_length=1, max_length=255)
    description: str | None = None
    stages: list[StageInput] = PField(min_length=1)


class MissionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    stages: list[StageInput] | None = None


class MissionAssignBody(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionDispatchBody(BaseModel):
    robot_id: str = PField(min_length=1)


class MissionView(BaseModel):
    mission_id: UUID
    update_id: int
    name: str
    description: str | None
    stages: list[Stage]
    status: MissionStatus
    robot_id: str | None
    dispatched_at: datetime | None
    created_at: datetime
    updated_at: datetime
    failure_errors: list[MissionError] | None = None
