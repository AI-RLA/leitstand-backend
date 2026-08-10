"""In-process pub/sub bus.

Producers (Zenoh callbacks on the Zenoh thread) call publish;
consumers (asyncio coroutines, e.g. the WS hub) subscribe and
await an asyncio.Queue. publish uses call_soon_threadsafe to
enqueue across the thread boundary. Bounded per-consumer queues;
a slow consumer never blocks a producer. On backpressure, a
latched (freshness-first) event evicts the queue's oldest entry
to admit the incoming value; a non-latched (discrete) event is
dropped instead, preserving delivery order for the rest. Eviction
is queue-global (the oldest entry, whatever its topic), so on a
multi-topic subscription it can drop an older entry of another
topic; a per-topic latest-wins policy is a future refinement.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any

import structlog

from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.event_subscriber import EventSubscriber

logger = structlog.get_logger(__name__)


class EventBus(EventPublisher, EventSubscriber):
    def __init__(self, queue_maxsize: int = 256) -> None:
        self._queue_maxsize = queue_maxsize
        self._consumers: dict[str, list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = (
            defaultdict(list)
        )
        self._latched: dict[str, Any] = {}
        self._lock = threading.Lock()

    def subscribe(self, topic_prefix: str) -> asyncio.Queue:
        # Caller is on the asyncio loop thread; latched put_nowait below is
        # safe without call_soon_threadsafe.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        with self._lock:
            self._consumers[topic_prefix].append((queue, loop))
            for topic, value in self._latched.items():
                if _topic_matches(topic, topic_prefix):
                    try:
                        queue.put_nowait({"topic": topic, "payload": value})
                    except asyncio.QueueFull:
                        break
        return queue

    def unsubscribe(self, topic_prefix: str, queue: asyncio.Queue) -> None:
        with self._lock:
            consumers = self._consumers.get(topic_prefix)
            if consumers:
                self._consumers[topic_prefix] = [
                    (q, loop) for q, loop in consumers if q is not queue
                ]

    def publish(self, topic: str, payload: Any, latch: bool = True) -> None:
        # Hold the lock across call_soon_threadsafe so a concurrent
        # unsubscribe cannot orphan an already-scheduled enqueue, and so
        # snapshot delivery in subscribe() can't be interleaved.
        with self._lock:
            if latch:
                self._latched[topic] = payload
            for prefix, consumers in self._consumers.items():
                if not _topic_matches(topic, prefix):
                    continue
                for q, loop in consumers:
                    try:
                        loop.call_soon_threadsafe(self._enqueue, q, topic, payload, latch)
                    except RuntimeError:
                        pass

    def unlatch(self, topic_prefix: str) -> None:
        with self._lock:
            for topic in list(self._latched.keys()):
                if _topic_matches(topic, topic_prefix):
                    del self._latched[topic]

    def latched(self, topic: str) -> Any | None:
        with self._lock:
            return self._latched.get(topic)

    def _enqueue(self, q: asyncio.Queue, topic: str, payload: Any, latch: bool) -> None:
        item = {"topic": topic, "payload": payload}
        try:
            q.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        if latch:
            # A consumer this far behind has already missed intermediate samples, so
            # evicting one more stale entry to admit the freshest value loses nothing
            # it hadn't already lost; the alternative, dropping the newest, would leave
            # the consumer's view stuck in the past indefinitely.
            try:
                q.get_nowait()
                q.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            else:
                logger.warning("event_bus_queue_full_evicted_oldest", topic=topic)
                return
        logger.warning("event_bus_queue_full_dropped_newest", topic=topic)


def _topic_matches(topic: str, prefix: str) -> bool:
    """Segment-aware prefix match: the prefix itself, or a path continuing after a '/'.

    So ``events/robot`` matches ``events/robot/r1/pose`` but never ``events/robotXYZ``.
    """
    return topic == prefix or topic.startswith(prefix + "/")
