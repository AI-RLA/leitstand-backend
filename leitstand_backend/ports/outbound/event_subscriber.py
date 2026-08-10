"""EventSubscriber driven port: consume events from the in-process bus."""

import asyncio
from abc import ABC, abstractmethod


class EventSubscriber(ABC):
    """Read side of the event bus: subscribe to a topic prefix, drain a queue."""

    @abstractmethod
    def subscribe(self, topic_prefix: str) -> asyncio.Queue:
        """Return a queue that receives ``{topic, payload}`` for matching events."""

    @abstractmethod
    def unsubscribe(self, topic_prefix: str, queue: asyncio.Queue) -> None:
        """Stop delivering to a queue previously returned by ``subscribe``."""
