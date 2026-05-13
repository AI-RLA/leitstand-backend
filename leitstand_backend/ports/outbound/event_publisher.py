"""EventPublisher driven port: publish events out of the application."""

from abc import ABC, abstractmethod


class EventPublisher(ABC):
    @abstractmethod
    def publish(self, topic: str, payload: dict, latch: bool = False) -> None: ...

    @abstractmethod
    def unlatch(self, prefix: str) -> None: ...
