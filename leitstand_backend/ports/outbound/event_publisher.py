"""EventPublisher driven port: publish events out of the application, and the topic scheme.

Every real-time topic is ``events/<entity>/<id>/<kind>``. Publishers and latched readers build
topics here so they cannot drift; a mismatch would make a latched read return None. Matching
on the bus is segment-aware: a subscription to ``events/robot`` matches ``events/robot/r1/pose``
but never ``events/robotXYZ``.
"""

from abc import ABC, abstractmethod
from uuid import UUID

REGISTRY = "events/registry"


def robot_topic(robot_id: str, kind: str) -> str:
    """Per-robot topic. ``kind`` is pose | battery | factsheet | status."""
    return f"events/robot/{robot_id}/{kind}"


def robot_aggregate_topic(robot_id: str) -> str:
    """Merged per-robot stream (all kinds under one robot)."""
    return f"events/robot/{robot_id}"


def mission_topic(mission_id: UUID, kind: str) -> str:
    """Per-mission topic. ``kind`` is state | lifecycle."""
    return f"events/mission/{mission_id}/{kind}"


class EventPublisher(ABC):
    @abstractmethod
    def publish(self, topic: str, payload: dict, latch: bool = False) -> None: ...

    @abstractmethod
    def unlatch(self, prefix: str) -> None: ...
