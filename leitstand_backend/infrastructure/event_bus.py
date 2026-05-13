"""In-process pub/sub bus.

Producers (Zenoh callbacks on the Zenoh thread) call publish;
consumers (asyncio coroutines, e.g. the WS hub) subscribe and
await an asyncio.Queue. publish uses call_soon_threadsafe to
enqueue across the thread boundary. Bounded per-consumer queues;
slow consumers are dropped silently rather than blocking.
"""

from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from typing import Any

from leitstand_backend.ports.outbound.event_publisher import EventPublisher


class EventBus(EventPublisher):
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
                if topic.startswith(topic_prefix):
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
                if not topic.startswith(prefix):
                    continue
                for q, loop in consumers:
                    try:
                        loop.call_soon_threadsafe(_enqueue, q, topic, payload)
                    except RuntimeError:
                        pass

    def unlatch(self, topic_prefix: str) -> None:
        with self._lock:
            for topic in list(self._latched.keys()):
                if topic.startswith(topic_prefix):
                    del self._latched[topic]

    def latched(self, topic: str) -> Any | None:
        with self._lock:
            return self._latched.get(topic)


def _enqueue(q: asyncio.Queue, topic: str, payload: Any) -> None:
    try:
        q.put_nowait({"topic": topic, "payload": payload})
    except asyncio.QueueFull:
        pass
