"""In-memory EventPublisher fake (test-only)."""

from __future__ import annotations

from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class InMemoryEventPublisher(EventPublisher):
    def __init__(self) -> None:
        self.published: list[tuple[str, dict, bool]] = []
        self.unlatched: list[str] = []

    def publish(self, topic: str, payload: dict, latch: bool = False) -> None:
        self.published.append((topic, payload, latch))

    def unlatch(self, prefix: str) -> None:
        self.unlatched.append(prefix)
