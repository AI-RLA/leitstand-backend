"""Mission lifecycle state-machine tests."""

import pytest

from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.mission import MissionStatus
from leitstand_backend.domain.model.mission.mission_lifecycle import (
    ALLOWED_TRANSITIONS,
    EXECUTING_STATES,
    TERMINAL_STATES,
    MissionTrigger,
    is_executing,
    is_terminal,
    next_state,
)


@pytest.mark.parametrize(
    ("current", "trigger", "expected"),
    [(current, trigger, expected) for (current, trigger), expected in ALLOWED_TRANSITIONS.items()],
)
def test_allowed_transitions(current, trigger, expected):
    assert next_state(current, trigger) == expected


@pytest.mark.parametrize(
    ("current", "trigger"),
    [
        (MissionStatus.DRAFT, MissionTrigger.ACK),
        (MissionStatus.DRAFT, MissionTrigger.COMPLETE),
        (MissionStatus.SUCCEEDED, MissionTrigger.CANCEL),
        (MissionStatus.FAILED, MissionTrigger.RESUME),
        (MissionStatus.CANCELLED, MissionTrigger.DISPATCH),
        (MissionStatus.RUNNING, MissionTrigger.ASSIGN),
    ],
)
def test_invalid_transitions_raise(current, trigger):
    with pytest.raises(InvalidMissionTransition) as excinfo:
        next_state(current, trigger)
    assert excinfo.value.current == current
    assert excinfo.value.trigger == trigger


def test_terminal_states_have_no_outbound_transitions():
    # SUCCEEDED is fully terminal. FAILED and CANCELLED allow "reset" back to DRAFT.
    resettable = {MissionStatus.FAILED, MissionStatus.CANCELLED}
    for state in TERMINAL_STATES:
        outbound = {trigger for (current, trigger) in ALLOWED_TRANSITIONS if current == state}
        if state in resettable:
            assert outbound == {MissionTrigger.RESET}, (
                f"{state} should only allow RESET, got: {outbound}"
            )
        else:
            assert outbound == set(), f"{state} has outbound transitions: {outbound}"


def test_terminal_states_set_matches_predicate():
    for state in MissionStatus:
        assert is_terminal(state) is (state in TERMINAL_STATES)


def test_terminal_states_cover_expected_set():
    assert TERMINAL_STATES == {
        MissionStatus.SUCCEEDED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    }


def test_mission_trigger_enum_matches_transition_table():
    """The MissionTrigger enum must declare exactly the triggers the table uses.

    Guards against drift: a trigger in the table but missing from the enum (or a
    declared-but-dead trigger) is a contract bug.
    """
    declared = set(MissionTrigger)
    in_table = {trigger for (_current, trigger) in ALLOWED_TRANSITIONS}
    assert declared == in_table


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (MissionStatus.DISPATCHED, True),
        (MissionStatus.RUNNING, True),
        (MissionStatus.PAUSED, True),
        (MissionStatus.DRAFT, False),
        (MissionStatus.ASSIGNED, False),
        (MissionStatus.SUCCEEDED, False),
        (MissionStatus.FAILED, False),
        (MissionStatus.CANCELLED, False),
    ],
)
def test_is_executing(state, expected):
    assert is_executing(state) is expected


def test_executing_states_are_disjoint_from_terminal_states():
    assert EXECUTING_STATES.isdisjoint(TERMINAL_STATES)
