"""Unit tests for RobotStatusProjector event handling (fake read model + real bus)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.robot.robot_status import RobotStatus
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.infrastructure.robot_status_projector import (
    RobotStatusProjector,
    RobotStatusReadModel,
)


class FakeReadModel(RobotStatusReadModel):
    def __init__(
        self,
        statuses: dict[str, RobotStatus | None],
        assignments: dict[UUID, str] | None = None,
        raises_for: set[str] | None = None,
    ) -> None:
        self._statuses = statuses
        self._assignments = assignments or {}
        self._raises_for = raises_for or set()
        self.compute_calls: list[str] = []

    async def assigned_robot(self, mission_id: UUID) -> str | None:
        return self._assignments.get(mission_id)

    async def compute_status(self, robot_id: str) -> RobotStatus | None:
        self.compute_calls.append(robot_id)
        if robot_id in self._raises_for:
            raise RuntimeError("compute boom")
        return self._statuses.get(robot_id)

    async def all_robot_ids(self) -> list[str]:
        return list(self._statuses.keys())


def _projector(bus: EventBus, read_model: RobotStatusReadModel) -> RobotStatusProjector:
    return RobotStatusProjector(
        subscriber=bus,
        publisher=bus,
        read_model=read_model,
        loop=asyncio.get_running_loop(),
    )


async def _next_status(queue: asyncio.Queue) -> dict:
    return await asyncio.wait_for(queue.get(), timeout=1.0)


@pytest.mark.asyncio
async def test_registry_event_publishes_status() -> None:
    bus = EventBus()
    rm = FakeReadModel({"r1": RobotStatus.IDLE})
    proj = _projector(bus, rm)
    status_q = bus.subscribe(event_topics.robot_topic("r1", "status"))
    proj.start()
    try:
        bus.publish(event_topics.REGISTRY, {"type": "robot.online", "robot_id": "r1"}, latch=False)
        event = await _next_status(status_q)
        assert event["topic"] == "events/robot/r1/status"
        assert event["payload"] == {"status": "idle"}
    finally:
        await proj.aclose()


@pytest.mark.asyncio
async def test_lifecycle_event_resolves_robot_via_read_model() -> None:
    bus = EventBus()
    mission_id = uuid4()
    rm = FakeReadModel({"r2": RobotStatus.ACTIVE}, assignments={mission_id: "r2"})
    proj = _projector(bus, rm)
    status_q = bus.subscribe(event_topics.robot_topic("r2", "status"))
    proj.start()
    try:
        bus.publish(
            event_topics.mission_topic(mission_id, "lifecycle"),
            {"mission_id": str(mission_id), "status": "DISPATCHED", "trigger": "dispatch"},
            latch=False,
        )
        event = await _next_status(status_q)
        assert event["payload"] == {"status": "active"}
    finally:
        await proj.aclose()


@pytest.mark.asyncio
async def test_publish_on_change_suppresses_duplicate() -> None:
    bus = EventBus()
    rm = FakeReadModel({"r1": RobotStatus.IDLE, "r2": RobotStatus.ACTIVE})
    proj = _projector(bus, rm)
    status_q = bus.subscribe("events/robot")
    proj.start()
    try:
        # r1 -> idle (published), r1 again -> idle (suppressed), r2 -> active (published).
        # All three ride the registry FIFO queue, so the received order is deterministic.
        bus.publish(event_topics.REGISTRY, {"robot_id": "r1"}, latch=False)
        bus.publish(event_topics.REGISTRY, {"robot_id": "r1"}, latch=False)
        bus.publish(event_topics.REGISTRY, {"robot_id": "r2"}, latch=False)

        first = await _next_status(status_q)
        second = await _next_status(status_q)
        assert (first["topic"], first["payload"]) == ("events/robot/r1/status", {"status": "idle"})
        assert (second["topic"], second["payload"]) == (
            "events/robot/r2/status",
            {"status": "active"},
        )
        # The duplicate r1 event was computed but not republished.
        assert rm.compute_calls == ["r1", "r1", "r2"]
        assert status_q.empty()
    finally:
        await proj.aclose()


@pytest.mark.asyncio
async def test_non_lifecycle_mission_topic_is_ignored() -> None:
    bus = EventBus()
    mission_id = uuid4()
    rm = FakeReadModel({"r2": RobotStatus.ACTIVE}, assignments={mission_id: "r2"})
    proj = _projector(bus, rm)
    status_q = bus.subscribe("events/robot")
    proj.start()
    try:
        # A .../state frame must not trigger a recompute; the following lifecycle event must.
        bus.publish(event_topics.mission_topic(mission_id, "state"), {"x": 1}, latch=False)
        bus.publish(
            event_topics.mission_topic(mission_id, "lifecycle"),
            {"mission_id": str(mission_id), "status": "DISPATCHED", "trigger": "dispatch"},
            latch=False,
        )
        event = await _next_status(status_q)
        assert event["topic"] == "events/robot/r2/status"
        assert rm.compute_calls == ["r2"]  # the /state frame never reached compute
    finally:
        await proj.aclose()


@pytest.mark.asyncio
async def test_failing_compute_does_not_kill_the_loop() -> None:
    bus = EventBus()
    rm = FakeReadModel({"bad": RobotStatus.IDLE, "r1": RobotStatus.ACTIVE}, raises_for={"bad"})
    proj = _projector(bus, rm)
    status_q = bus.subscribe(event_topics.robot_topic("r1", "status"))
    proj.start()
    try:
        bus.publish(event_topics.REGISTRY, {"robot_id": "bad"}, latch=False)
        bus.publish(event_topics.REGISTRY, {"robot_id": "r1"}, latch=False)
        event = await _next_status(status_q)
        assert event["payload"] == {"status": "active"}  # loop survived the boom
    finally:
        await proj.aclose()


@pytest.mark.asyncio
async def test_recompute_all_covers_every_robot() -> None:
    bus = EventBus()
    rm = FakeReadModel({"r1": RobotStatus.IDLE, "r2": RobotStatus.ACTIVE})
    proj = _projector(bus, rm)
    status_q = bus.subscribe("events/robot")

    await proj.recompute_all()

    received = {}
    for _ in range(2):
        event = await _next_status(status_q)
        received[event["topic"]] = event["payload"]
    assert received == {
        "events/robot/r1/status": {"status": "idle"},
        "events/robot/r2/status": {"status": "active"},
    }
