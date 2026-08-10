"""Robot persistence Port."""

from __future__ import annotations

from abc import ABC, abstractmethod

from leitstand_backend.domain.model.robot.robot import Metadata, Robot


class RobotRepository(ABC):
    """Persistence for the Robot aggregate."""

    @abstractmethod
    async def get(self, robot_id: str) -> Robot | None: ...

    @abstractmethod
    async def record_online(self, robot_id: str, metadata: Metadata) -> Robot: ...

    @abstractmethod
    async def record_offline(self, robot_id: str) -> Robot | None: ...

    @abstractmethod
    async def list(self) -> list[Robot]: ...

    @abstractmethod
    async def mark_all_offline(self) -> None:
        """Bulk-set every row's ``online`` flag to false.

        Called once at startup before the Zenoh subscriber is
        declared. History-replay PUT events will flip
        actually-online rows back to true via ``record_online``.
        """

    @abstractmethod
    async def save_telemetry(self, robot_id: str, kind: str, payload: dict) -> None:
        """Persist last-known telemetry snapshot for a robot.

        ``kind`` is one of ``"pose"``, ``"battery"``, ``"state"``.
        No-op if the robot row does not exist yet.
        """
