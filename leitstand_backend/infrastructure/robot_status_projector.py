"""Robot status projector: derive each robot's operational status and push it live.

A read-model projector. It consumes the in-process events that can change a robot's
derived status -- connectivity (``events/registry``) and mission lifecycle
(``events/mission/<id>/lifecycle``) -- recomputes the affected robot's status, and
republishes it on ``events/robot/<id>/status`` (latched, publish-on-change). REST derives
the same value synchronously per request; this projector is the push path that keeps
WebSocket clients live.

It subscribes only to the registry and mission subtrees, not ``events/robot``: the
high-rate pose stream lives under ``events/robot`` and would share this consumer's
bounded queue, and the projector's own ``status`` output lives there too (a self-trigger
loop). Battery/charging is an input to the derivation but has no dedicated subscription
in v1 (real robots do not publish battery yet); the latest latched battery is still read
at recompute time, so charging is reflected whenever a recompute fires for another
reason. A periodic reconcile sweep heals any event dropped from a full queue under
telemetry load, making the read model eventually consistent.

The DB-backed reads sit behind ``RobotStatusReadModel`` so the event-handling logic is
testable without a database; the session-scoped implementation lives beside it.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leitstand_backend.adapters.outbound.persistence.postgres.mission_run_repository_adapter import (
    PostgresMissionRunRepositoryAdapter,
)
from leitstand_backend.adapters.outbound.persistence.postgres.robot_repository_adapter import (
    PostgresRobotRepositoryAdapter,
)
from leitstand_backend.application.robot_status_service import RobotStatusService
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.infrastructure.db import transactional_scope
from leitstand_backend.ports.outbound.event_publisher import EventPublisher
from leitstand_backend.ports.outbound.event_subscriber import EventSubscriber
from leitstand_backend.ports.outbound.robot_state_view import RobotStateView

logger = structlog.get_logger(__name__)

_RECONCILE_INTERVAL_S = 30.0


class RobotStatusReadModel(ABC):
    """The backend-owned facts the projector reads to derive and target status."""

    @abstractmethod
    async def compute_status(self, robot_id: str) -> RobotStatus | None:
        """Return a robot's derived status, or None when the robot is unknown."""

    @abstractmethod
    async def all_robot_ids(self) -> list[str]:
        """Return every known robot id (for the seed + reconcile sweeps)."""


class SessionScopedRobotStatusReadModel(RobotStatusReadModel):
    """Read model that opens a short-lived DB session per query.

    Mirrors the per-call session pattern used by the other session-scoped wrappers:
    the projector runs on the asyncio loop with no ambient session of its own.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        state_view: RobotStateView,
    ) -> None:
        self._session_factory = session_factory
        self._state_view = state_view

    async def compute_status(self, robot_id: str) -> RobotStatus | None:
        async with transactional_scope(self._session_factory) as session:
            service = RobotStatusService(
                PostgresRobotRepositoryAdapter(session),
                PostgresMissionRunRepositoryAdapter(session),
                self._state_view,
            )
            return await service.compute(robot_id)

    async def all_robot_ids(self) -> list[str]:
        async with transactional_scope(self._session_factory) as session:
            return [r.id for r in await PostgresRobotRepositoryAdapter(session).list()]


class RobotStatusProjector:
    """Subscribe to status-affecting events and publish each robot's derived status."""

    _REGISTRY_PREFIX = event_topics.REGISTRY
    _MISSION_PREFIX = "events/mission"

    def __init__(
        self,
        subscriber: EventSubscriber,
        publisher: EventPublisher,
        read_model: RobotStatusReadModel,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._sub = subscriber
        self._pub = publisher
        self._read = read_model
        self._loop = loop
        self._last: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._subs: list[tuple[str, asyncio.Queue]] = []
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        """Subscribe to the registry + mission subtrees and start the drain/reconcile tasks."""
        for prefix in (self._REGISTRY_PREFIX, self._MISSION_PREFIX):
            queue = self._sub.subscribe(prefix)
            self._subs.append((prefix, queue))
            self._tasks.append(self._loop.create_task(self._drain(queue)))
        self._tasks.append(self._loop.create_task(self._reconcile_loop()))

    async def _drain(self, queue: asyncio.Queue) -> None:
        while True:
            try:
                event = await queue.get()
                robot_id = await self._affected_robot(event["topic"], event["payload"])
                if robot_id is not None:
                    await self._recompute(robot_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad event must not kill the projector
                logger.exception("robot_status_projector_event_failed")

    async def _reconcile_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_RECONCILE_INTERVAL_S)
                await self.recompute_all()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("robot_status_projector_reconcile_failed")

    async def _affected_robot(self, topic: str, payload: object) -> str | None:
        """Resolve which robot a status-affecting event concerns, or None to ignore it."""
        if topic == self._REGISTRY_PREFIX:
            return payload.get("robot_id") if isinstance(payload, dict) else None
        segments = topic.split("/")
        # Only run lifecycle transitions move a robot between active and idle; the latched
        # .../state telemetry frame is ignored. The event names its robot, because the run's
        # robot need not be the mission's default one.
        if len(segments) >= 4 and segments[3] == "lifecycle":
            return payload.get("robot_id") if isinstance(payload, dict) else None
        return None

    async def _recompute(self, robot_id: str) -> None:
        async with self._lock:
            status = await self._read.compute_status(robot_id)
            if status is None or self._last.get(robot_id) == status.value:
                return
            self._last[robot_id] = status.value
            self._pub.publish(
                event_topics.robot_topic(robot_id, "status"),
                {"status": status.value},
                latch=True,
            )

    async def recompute_all(self) -> None:
        """Recompute every known robot. Used after the DB seed and by the reconcile sweep."""
        # Sequential: the pool is small (default 10); a gather would risk exhausting it.
        for robot_id in await self._read.all_robot_ids():
            await self._recompute(robot_id)

    async def aclose(self) -> None:
        # Await the cancelled tasks before returning so no in-flight recompute is
        # still holding a DB session when the shared engine is disposed downstream.
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for prefix, queue in self._subs:
            self._sub.unsubscribe(prefix, queue)
