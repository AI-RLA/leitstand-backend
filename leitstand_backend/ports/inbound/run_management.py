"""Driving port: starting, steering and annotating runs of a mission."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.domain.model.mission.mission_run import (
    MissionRun,
    RunOrigin,
)


class StartRunCommand(BaseModel):
    mission_id: UUID
    robot_id: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    allow_concurrent: bool = Field(
        default=False,
        description=(
            "Start this run although another run of the same mission is active, on another robot."
        ),
    )
    # Filled by the web layer from the request's provenance; the service persists it on the run.
    origin: RunOrigin


class DispatchOutcome(str, Enum):
    """How the dispatch call ended, as seen by the boundary that made it.

    ``ERROR`` is a failure of the call itself (transport, encoding), and ``TIMEOUT`` is no
    answer at all. Neither says the robot declined, so neither ends the run: the caller is told
    the dispatch is unconfirmed and the run waits for the robot's frames.
    """

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


class SettleRunCommand(BaseModel):
    run_id: UUID
    outcome: DispatchOutcome
    reason: str | None = None
    replied_at: datetime


class CancelRunCommand(BaseModel):
    mission_id: UUID
    run_id: UUID | None = None
    mode: CancelMode = CancelMode.GRACEFUL


class CloseRunCommand(BaseModel):
    """End a run whose robot is offline, on the operator's word rather than the robot's report."""

    mission_id: UUID
    run_id: UUID | None = None


class PauseRunCommand(BaseModel):
    mission_id: UUID
    run_id: UUID | None = None


class ResumeRunCommand(BaseModel):
    mission_id: UUID
    run_id: UUID | None = None


class AnnotateRunCommand(BaseModel):
    run_id: UUID
    notes: str | None = Field(default=None, max_length=4000)


class DeleteRunCommand(BaseModel):
    run_id: UUID


class RunManagementUseCase(ABC):
    """Starting a run is two calls around the robot RPC, driven by the orchestrator that owns the sessions.

    ``prepare`` validates and commits the run as PENDING; the boundary then calls the robot with
    no transaction held; ``settle`` records the reply. Everything else is one transaction.
    """

    @abstractmethod
    async def prepare(self, command: StartRunCommand) -> MissionRun: ...

    @abstractmethod
    async def settle(self, command: SettleRunCommand) -> MissionRun: ...

    @abstractmethod
    async def cancel(self, command: CancelRunCommand) -> MissionRun: ...

    @abstractmethod
    async def close(self, command: CloseRunCommand) -> MissionRun:
        """Close a run whose robot is offline; refused while the robot can answer a cancel."""

    @abstractmethod
    async def pause(self, command: PauseRunCommand) -> MissionRun: ...

    @abstractmethod
    async def resume(self, command: ResumeRunCommand) -> MissionRun: ...

    @abstractmethod
    async def annotate(self, command: AnnotateRunCommand) -> MissionRun: ...

    @abstractmethod
    async def delete(self, command: DeleteRunCommand) -> None:
        """Remove a finished run. An active one is refused; the audit log keeps the attempt."""
