"""Outbound port: dispatch missions to a robot over the wire."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import Mission
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode


class MissionDispatcher(ABC):
    """Send mission lifecycle commands to a specific robot.

    Implementations are responsible for wire-format encoding and
    transport (e.g. JSON over a Zenoh queryable). Cancel/pause/resume
    are fire-and-confirm: success is observed via the state channel,
    not via the return value.
    """

    @abstractmethod
    async def dispatch(self, mission: Mission, robot_id: str) -> None:
        """Send ``mission`` to ``robot_id``.

        Raises :class:`MissionRejectedByRobot` if the robot replies
        with ``accepted=False``, or :class:`MissionDispatchTimeout`
        if no reply arrives within the implementation's timeout.
        """

    @abstractmethod
    async def cancel(
        self,
        mission_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> None: ...

    @abstractmethod
    async def pause(self, mission_id: UUID, robot_id: str) -> None: ...

    @abstractmethod
    async def resume(self, mission_id: UUID, robot_id: str) -> None: ...
