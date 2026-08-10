"""Transaction-bound event publishing: hold bus events until the transaction commits.

A producer that emits events inside a DB transaction publishes through this, so events reach the
bus only after the write commits, and not at all if it rolls back. Events emitted outside a
transaction (e.g. telemetry) keep publishing to the bus directly.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from leitstand_backend.infrastructure.db import register_after_commit
from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class TransactionBoundEventPublisher(EventPublisher):
    """Defer each bus publish until the session's transaction commits.

    Records each call as an after-commit callback on the session instead of publishing
    immediately; the commit boundary runs them, and a rollback discards them. Implements the
    ``EventPublisher`` port, so producers are unchanged.
    """

    def __init__(self, session: AsyncSession, bus: EventPublisher) -> None:
        self._session = session
        self._bus = bus

    def publish(self, topic: str, payload: dict, latch: bool = False) -> None:
        register_after_commit(self._session, lambda: self._bus.publish(topic, payload, latch))

    def unlatch(self, prefix: str) -> None:
        register_after_commit(self._session, lambda: self._bus.unlatch(prefix))
