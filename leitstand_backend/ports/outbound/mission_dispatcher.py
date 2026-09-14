"""Outbound port: dispatch a run to a robot over the wire."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode, ControlRefusal


@dataclass(frozen=True)
class ControlReply:
    """The robot's receipt for a pause, resume or cancel.

    ``applied`` is None when no reply arrived in time. ``refusal`` and ``reason`` are set when
    the robot answered that it did not apply the command.
    """

    applied: bool | None
    refusal: ControlRefusal | None = None
    reason: str | None = None


class MissionDispatcher(ABC):
    """Send run lifecycle commands to a specific robot.

    The robot is told the run id, never the mission's: a run is one execution, and the robot
    keys duplicate detection, cancel matching and state reports on it. Implementations own wire
    encoding and transport. Cancel, pause and resume return the robot's receipt; whether the
    command took effect shows up on the state channel.
    """

    @abstractmethod
    async def dispatch(self, run_id: UUID, stages: list[Stage], robot_id: str) -> None:
        """Send the run to ``robot_id``.

        Raises :class:`MissionRejectedByRobot` if the robot replies with ``accepted=False``, or
        :class:`MissionDispatchTimeout` if no reply arrives within the implementation's timeout.
        """

    @abstractmethod
    async def cancel(
        self,
        run_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> ControlReply: ...

    @abstractmethod
    async def pause(self, run_id: UUID, robot_id: str) -> ControlReply: ...

    @abstractmethod
    async def resume(self, run_id: UUID, robot_id: str) -> ControlReply: ...
