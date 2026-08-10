"""EventBus segment-aware topic matching."""

from __future__ import annotations

import asyncio

import pytest

from leitstand_backend.infrastructure.event_bus import EventBus


async def _drain_one(queue: asyncio.Queue, timeout: float = 0.1):
    return await asyncio.wait_for(queue.get(), timeout=timeout)


@pytest.mark.asyncio
async def test_prefix_matches_path_continuing_after_slash():
    bus = EventBus()
    q = bus.subscribe("events/robot")
    bus.publish("events/robot/r1/pose", {"v": 1}, latch=False)
    event = await _drain_one(q)
    assert event["topic"] == "events/robot/r1/pose"


@pytest.mark.asyncio
async def test_prefix_matches_itself_exactly():
    bus = EventBus()
    q = bus.subscribe("events/registry")
    bus.publish("events/registry", {"type": "robot.online"}, latch=False)
    event = await _drain_one(q)
    assert event["topic"] == "events/registry"


@pytest.mark.asyncio
async def test_prefix_does_not_match_sibling_with_shared_string_prefix():
    # The classic startswith() false-match: 'events/robot' must NOT match
    # 'events/robotXYZ/...'. Segment-aware matching requires a '/' boundary.
    bus = EventBus()
    q = bus.subscribe("events/robot")
    bus.publish("events/robotXYZ/pose", {"v": 1}, latch=False)
    with pytest.raises(asyncio.TimeoutError):
        await _drain_one(q, timeout=0.05)


@pytest.mark.asyncio
async def test_exact_state_topic_excludes_sibling_completed():
    # Mirrors the frontend H7 fix: subscribing to the exact .../state topic must
    # not receive the sibling .../completed frame.
    bus = EventBus()
    q = bus.subscribe("events/mission/m1/state")
    bus.publish("events/mission/m1/completed", {"status": "SUCCEEDED"}, latch=False)
    with pytest.raises(asyncio.TimeoutError):
        await _drain_one(q, timeout=0.05)


@pytest.mark.asyncio
async def test_latched_backpressure_evicts_oldest_to_admit_newest():
    # A slow consumer must see the freshest latched sample, not get stuck on a
    # backlog while the true latest value sits unused in `_latched`.
    bus = EventBus(queue_maxsize=2)
    q = bus.subscribe("events/robot/r1/pose")
    for i in range(4):
        bus.publish("events/robot/r1/pose", {"seq": i}, latch=True)

    # The first drain's await flushes the scheduled enqueues; draining the two
    # newest samples (2, 3) rather than the stale head (0, 1) proves eviction.
    first = await _drain_one(q)
    second = await _drain_one(q)
    assert [first["payload"]["seq"], second["payload"]["seq"]] == [2, 3]


@pytest.mark.asyncio
async def test_non_latched_backpressure_drops_newest_and_keeps_order():
    # Discrete lifecycle-style events keep FIFO drop-newest: no reordering or
    # silent loss of the events that did make it into the queue.
    bus = EventBus(queue_maxsize=2)
    q = bus.subscribe("events/mission/m1/lifecycle")
    for i in range(4):
        bus.publish("events/mission/m1/lifecycle", {"seq": i}, latch=False)

    # Draining the two oldest samples (0, 1) proves the newest were dropped and
    # the queued order was preserved.
    first = await _drain_one(q)
    second = await _drain_one(q)
    assert [first["payload"]["seq"], second["payload"]["seq"]] == [0, 1]
