"""Dispatch envelopes: send-goal request/response and cancel request."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission import Mission


class MissionDispatchRequest(BaseModel):
    """Backend -> robot: send a mission to be executed."""

    mission: Mission


class MissionDispatchResponse(BaseModel):
    """Robot -> backend: acknowledge or reject a dispatch request."""

    accepted: bool
    reason: str | None = Field(
        default=None,
        description="Required when ``accepted`` is False; human-readable rejection cause.",
    )


class CancelMode(str, Enum):
    """How the robot should react to a cancel request."""

    GRACEFUL = "graceful"
    IMMEDIATE = "immediate"


class CancelRequest(BaseModel):
    """Backend -> robot: cancel the active mission."""

    mission_id: UUID
    mode: CancelMode = CancelMode.GRACEFUL
