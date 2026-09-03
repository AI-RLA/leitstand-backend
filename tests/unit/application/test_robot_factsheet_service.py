"""RobotFactsheetService: the declaration is persisted as well as latched.

The factsheet is read at dispatch time to pre-validate a mission against the robot, but the view
serves it from the in-process latch. Without a durable copy the backend forgets what every robot
can do whenever it restarts, and cannot validate a dispatch until that robot next reconnects.
"""

from __future__ import annotations

import pytest

from leitstand_backend.application.robot_factsheet_service import RobotFactsheetService
from leitstand_backend.domain import event_topics
from leitstand_backend.domain.model.robot.robot_factsheet import (
    NavigationCapability,
    RobotFactsheet,
    WaypointKind,
)
from leitstand_backend.infrastructure.event_bus import EventBus
from leitstand_backend.infrastructure.factsheet_view import EventBusBackedRobotFactsheetView
from leitstand_backend.ports.inbound.robot_factsheet import RecordRobotFactsheetCommand
from tests.fakes.in_memory_event_publisher import InMemoryEventPublisher
from tests.fakes.in_memory_robot_repository import InMemoryRobotRepository

ROBOT_ID = "bonirob2"


def _factsheet() -> RobotFactsheet:
    return RobotFactsheet(
        robot_id=ROBOT_ID,
        navigation=NavigationCapability(supported_waypoint_kinds=[WaypointKind.WGS84]),
    )


@pytest.mark.asyncio
async def test_record_persists_and_latches_the_factsheet():
    repo, events = InMemoryRobotRepository(), InMemoryEventPublisher()
    svc = RobotFactsheetService(repo=repo, events=events)

    await svc.record(RecordRobotFactsheetCommand(factsheet=_factsheet()))

    assert repo.factsheets[ROBOT_ID]["robot_id"] == ROBOT_ID
    (topic, payload, latch) = events.published[0]
    assert topic == event_topics.robot_topic(ROBOT_ID, "factsheet")
    assert latch is True
    # The two copies must not diverge: the startup seed republishes the row onto this topic.
    assert payload == repo.factsheets[ROBOT_ID]


@pytest.mark.asyncio
async def test_a_failed_persist_leaves_no_latch():
    """Persisting first is what makes a crash between the two recoverable.

    A durable factsheet the next startup re-latches is survivable; a latch with no row behind it
    is a capability claim that disappears on restart and cannot be reproduced.
    """
    repo, events = InMemoryRobotRepository(), InMemoryEventPublisher()

    async def failing_save(robot_id: str, payload: dict) -> None:
        raise RuntimeError("database unreachable")

    repo.save_factsheet = failing_save  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await RobotFactsheetService(repo=repo, events=events).record(
            RecordRobotFactsheetCommand(factsheet=_factsheet())
        )

    assert events.published == []


def test_a_latched_payload_is_readable_as_a_factsheet():
    """Pins the contract the startup seed relies on.

    ``_seed_event_bus_from_db`` republishes ``robots.factsheet_json`` onto this topic and nothing
    else; if the topic or the serialised shape drifted, a restarted backend would silently hold no
    factsheet for a robot that has one persisted.
    """
    bus = EventBus()
    view = EventBusBackedRobotFactsheetView(bus)
    bus.publish(
        event_topics.robot_topic(ROBOT_ID, "factsheet"),
        _factsheet().model_dump(mode="json"),
        latch=True,
    )

    restored = view.latest(ROBOT_ID)

    assert restored is not None
    assert restored.robot_id == ROBOT_ID
    assert restored.navigation is not None
    assert restored.navigation.supported_waypoint_kinds == [WaypointKind.WGS84]


def test_a_factsheet_this_backend_cannot_read_costs_only_that_robot():
    """A robot ahead of or behind the contract must not hide the rest of the fleet.

    The fleet view asks for every robot's factsheet while building its list, so raising here would
    turn one unreadable declaration into an empty fleet.
    """
    bus = EventBus()
    view = EventBusBackedRobotFactsheetView(bus)
    bus.publish(
        event_topics.robot_topic(ROBOT_ID, "factsheet"),
        {"robot_id": ROBOT_ID, "physical_parameters": {"width_m": 0.58}},
        latch=True,
    )

    assert view.latest(ROBOT_ID) is None


def test_latest_is_none_for_a_robot_that_never_declared_one():
    assert EventBusBackedRobotFactsheetView(EventBus()).latest("never-seen") is None
