"""Fake MissionDispatcher with configurable behaviour (test-only)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from leitstand_backend.domain.model.mission.mission import Stage
from leitstand_backend.domain.model.mission.mission_dispatch import CancelMode
from leitstand_backend.ports.outbound.mission_dispatcher import ControlReply, MissionDispatcher


class FakeMissionDispatcher(MissionDispatcher):
    """In-memory dispatcher that records calls and can be told to reject, time out, or wait.

    Use :meth:`set_dispatch_handler` to inject custom dispatch behaviour (e.g. raise
    ``MissionRejectedByRobot``). Set :attr:`gate` to an :class:`asyncio.Event` to hold the
    dispatch RPC open until a test releases it, which is how a cancel arriving mid-dispatch
    is exercised.
    """

    def __init__(self) -> None:
        self.dispatched: list[tuple[UUID, list[Stage], str]] = []
        self.cancelled: list[tuple[UUID, str, CancelMode]] = []
        self.paused: list[tuple[UUID, str]] = []
        self.resumed: list[tuple[UUID, str]] = []
        self.gate: asyncio.Event | None = None
        self._dispatch_handler: Callable[[UUID, list[Stage], str], Awaitable[None]] | None = None
        # The robot's receipt per command; tests set a refusal or silence (applied=None) here.
        self.replies: dict[str, ControlReply] = {
            "cancel": ControlReply(applied=True),
            "pause": ControlReply(applied=True),
            "resume": ControlReply(applied=True),
        }

    def set_dispatch_handler(
        self,
        handler: Callable[[UUID, list[Stage], str], Awaitable[None]] | None,
    ) -> None:
        self._dispatch_handler = handler

    async def dispatch(self, run_id: UUID, stages: list[Stage], robot_id: str) -> None:
        self.dispatched.append((run_id, stages, robot_id))
        if self.gate is not None:
            await self.gate.wait()
        if self._dispatch_handler is not None:
            await self._dispatch_handler(run_id, stages, robot_id)

    async def cancel(
        self,
        run_id: UUID,
        robot_id: str,
        mode: CancelMode = CancelMode.GRACEFUL,
    ) -> ControlReply:
        self.cancelled.append((run_id, robot_id, mode))
        return self.replies["cancel"]

    async def pause(self, run_id: UUID, robot_id: str) -> ControlReply:
        self.paused.append((run_id, robot_id))
        return self.replies["pause"]

    async def resume(self, run_id: UUID, robot_id: str) -> ControlReply:
        self.resumed.append((run_id, robot_id))
        return self.replies["resume"]
