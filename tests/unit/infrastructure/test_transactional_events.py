"""Unit tests for transaction-bound event publishing (no real DB).

A fake async session is enough: ``transactional_scope`` is plain Python around the session
(``commit``/``rollback`` + the ``info`` dict), so the after-commit ordering is fully observable.
"""

from __future__ import annotations

import pytest

from leitstand_backend.infrastructure.db import (
    register_after_commit,
    run_after_commit_callbacks,
    transactional_scope,
)
from leitstand_backend.infrastructure.transactional_events import TransactionBoundEventPublisher
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher


class _FakeSession:
    """AsyncSession stand-in: async context manager, ``info`` dict, order-recording commit/rollback."""

    def __init__(self, calls: list[str]) -> None:
        self.info: dict = {}
        self._calls = calls

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        self._calls.append("commit")

    async def rollback(self) -> None:
        self._calls.append("rollback")


def _factory(calls: list[str]):
    return lambda: _FakeSession(calls)


def _boom() -> None:
    raise RuntimeError("boom")


def test_publisher_buffers_until_drained() -> None:
    bus = InMemoryEventPublisher()
    session = _FakeSession([])
    pub = TransactionBoundEventPublisher(session, bus)

    pub.publish("events/x", {"a": 1}, latch=False)
    pub.unlatch("events/y")

    # Nothing reaches the bus yet; the calls are queued on the session.
    assert bus.published == []
    assert bus.unlatched == []
    assert len(session.info["after_commit_callbacks"]) == 2

    run_after_commit_callbacks(session)
    assert bus.published == [("events/x", {"a": 1}, False)]
    assert bus.unlatched == ["events/y"]
    assert "after_commit_callbacks" not in session.info  # cleared


def test_run_after_commit_is_fifo_and_exception_safe() -> None:
    session = _FakeSession([])
    order: list[int] = []
    register_after_commit(session, lambda: order.append(1))
    register_after_commit(session, _boom)
    register_after_commit(session, lambda: order.append(3))

    run_after_commit_callbacks(session)  # must not raise despite _boom
    assert order == [1, 3]  # FIFO; the failing callback did not drop the others


@pytest.mark.asyncio
async def test_transactional_scope_drains_only_after_commit() -> None:
    calls: list[str] = []
    bus = InMemoryEventPublisher()
    async with transactional_scope(_factory(calls)) as session:
        TransactionBoundEventPublisher(session, bus).publish("events/x", {}, latch=False)
        assert bus.published == []  # still inside the txn — nothing published
    assert calls == ["commit"]
    assert len(bus.published) == 1  # delivered after commit


@pytest.mark.asyncio
async def test_transactional_scope_discards_on_rollback() -> None:
    calls: list[str] = []
    bus = InMemoryEventPublisher()
    with pytest.raises(ValueError):
        async with transactional_scope(_factory(calls)) as session:
            TransactionBoundEventPublisher(session, bus).publish("events/x", {}, latch=False)
            raise ValueError("boom")
    assert calls == ["rollback"]
    assert bus.published == []  # rolled back → never published
