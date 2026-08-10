"""Fake MissionDispatcher with configurable behaviour (test-only)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import Mission
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.ports.outbound.mission_dispatcher import MissionDispatcher


class FakeMissionDispatcher(MissionDispatcher):
    """In-memory dispatcher that records calls and can be told to reject or time out.

    Use :meth:`set_dispatch_handler` to inject custom dispatch behaviour
    (e.g. raise ``MissionRejectedByRobot``) for specific test scenarios.
    """

    def __init__(self) -> None:
        self.dispatched: list[tuple[Mission, str]] = []
        self.cancelled: list[tuple[UUID, str, CancelMode]] = []
        self.paused: list[tuple[UUID, str]] = []
        self.resumed: list[tuple[UUID, str]] = []
        self._dispatch_handler: Callable[[Mission, str], Awaitable[None]] | None = None

    def set_dispatch_handler(
        self,
        handler: Callable[[Mission, str], Awaitable[None]],
    ) -> None:
        self._dispatch_handler = handler

    async def dispatch(self, mission: Mission, robot_id: str) -> None:
        self.dispatched.append((mission, robot_id))
        if self._dispatch_handler is not None:
            await self._dispatch_handler(mission, robot_id)

    async def cancel(
        self,
        mission_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> None:
        self.cancelled.append((mission_id, robot_id, mode))

    async def pause(self, mission_id: UUID, robot_id: str) -> None:
        self.paused.append((mission_id, robot_id))

    async def resume(self, mission_id: UUID, robot_id: str) -> None:
        self.resumed.append((mission_id, robot_id))
