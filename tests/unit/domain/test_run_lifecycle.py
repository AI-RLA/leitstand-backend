"""Run lifecycle state-machine tests."""

import pytest

from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.run_lifecycle import (
    ACTIVE_STATES,
    ALLOWED_TRANSITIONS,
    EXECUTING_STATES,
    TERMINAL_STATES,
    RunTrigger,
    is_active,
    is_executing,
    is_terminal,
    next_state,
    try_next_state,
)
from leitstand_backend.domain.model.mission.run_status import RunStatus

# Named here rather than derived from ALLOWED_TRANSITIONS, so that a table built by comprehension
# is checked against something written down independently of it.
_EXPECTED: dict[tuple[RunStatus, RunTrigger], RunStatus] = {
    (RunStatus.PENDING, RunTrigger.ACCEPT): RunStatus.DISPATCHED,
    (RunStatus.PENDING, RunTrigger.REJECT): RunStatus.REJECTED,
    (RunStatus.PENDING, RunTrigger.ACK): RunStatus.RUNNING,
    (RunStatus.DISPATCHED, RunTrigger.ACK): RunStatus.RUNNING,
    (RunStatus.DISPATCHED, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.RUNNING, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.RESUME): RunStatus.RUNNING,
    **{
        (state, trigger): expected
        for state in (
            RunStatus.PENDING,
            RunStatus.DISPATCHED,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
        )
        for trigger, expected in (
            (RunTrigger.COMPLETE, RunStatus.SUCCEEDED),
            (RunTrigger.FAIL, RunStatus.FAILED),
            (RunTrigger.CANCEL, RunStatus.CANCELLED),
            (RunTrigger.TIMEOUT, RunStatus.FAILED),
        )
    },
}


def test_the_table_holds_exactly_the_transitions_that_are_meant_to_exist():
    assert ALLOWED_TRANSITIONS == _EXPECTED


def test_a_dispatched_run_can_be_paused_without_having_been_seen_running():
    """A dropped RUNNING frame must not leave the run in a state nothing can move it out of.

    The robot pauses and repeats PAUSED on every heartbeat. Without this pair, the run would
    hold DISPATCHED for good: pause and resume would both refuse it, its frames would keep it
    looking fresh to reconciliation, and only a cancel would free the robot.
    """
    assert try_next_state(RunStatus.DISPATCHED, RunTrigger.PAUSE) is RunStatus.PAUSED
    assert next_state(RunStatus.PAUSED, RunTrigger.RESUME) is RunStatus.RUNNING


def test_a_pending_run_is_not_paused():
    """The operator's pause uses this trigger too, and no robot has accepted a PENDING run."""
    assert try_next_state(RunStatus.PENDING, RunTrigger.PAUSE) is None


@pytest.mark.parametrize(
    ("current", "trigger", "expected"), [(c, t, e) for (c, t), e in _EXPECTED.items()]
)
def test_allowed_transitions(current, trigger, expected):
    assert next_state(current, trigger) == expected


@pytest.mark.parametrize(
    ("current", "trigger"),
    [
        (RunStatus.SUCCEEDED, RunTrigger.CANCEL),
        (RunStatus.FAILED, RunTrigger.RESUME),
        (RunStatus.REJECTED, RunTrigger.ACK),
        (RunStatus.CANCELLED, RunTrigger.ACCEPT),
        (RunStatus.RUNNING, RunTrigger.ACCEPT),
        (RunStatus.PENDING, RunTrigger.PAUSE),
        (RunStatus.PAUSED, RunTrigger.ACK),
    ],
)
def test_invalid_transitions_raise(current, trigger):
    with pytest.raises(InvalidMissionTransition) as excinfo:
        next_state(current, trigger)
    assert excinfo.value.current == current
    assert excinfo.value.trigger == trigger


def test_terminal_states_have_no_outbound_transitions():
    # No RESET: a run is never reused, a mission is run again instead.
    for state in TERMINAL_STATES:
        outbound = {trigger for (current, trigger) in ALLOWED_TRANSITIONS if current == state}
        assert outbound == set(), f"{state} has outbound transitions: {outbound}"


def test_terminal_states_set_matches_predicate():
    for state in RunStatus:
        assert is_terminal(state) is (state in TERMINAL_STATES)


def test_terminal_states_cover_expected_set():
    assert TERMINAL_STATES == {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.REJECTED,
    }


def test_run_trigger_enum_matches_transition_table():
    declared = set(RunTrigger)
    in_table = {trigger for (_current, trigger) in ALLOWED_TRANSITIONS}
    assert declared == in_table


def test_the_robot_can_move_a_run_out_of_pending():
    # The executor starts before it replies to the dispatch query, so the first frame can
    # arrive before the reply has been recorded. It must count, not be dropped.
    assert next_state(RunStatus.PENDING, RunTrigger.ACK) is RunStatus.RUNNING
    assert next_state(RunStatus.PENDING, RunTrigger.COMPLETE) is RunStatus.SUCCEEDED
    assert next_state(RunStatus.PENDING, RunTrigger.FAIL) is RunStatus.FAILED


def test_a_rejection_before_the_reply_is_rejected_but_after_it_is_failed():
    assert next_state(RunStatus.PENDING, RunTrigger.REJECT) is RunStatus.REJECTED


@pytest.mark.parametrize(
    ("state", "executing", "active"),
    [
        (RunStatus.PENDING, False, True),
        (RunStatus.DISPATCHED, True, True),
        (RunStatus.RUNNING, True, True),
        (RunStatus.PAUSED, True, True),
        (RunStatus.SUCCEEDED, False, False),
        (RunStatus.FAILED, False, False),
        (RunStatus.CANCELLED, False, False),
        (RunStatus.REJECTED, False, False),
    ],
)
def test_is_executing_and_is_active(state, executing, active):
    assert is_executing(state) is executing
    assert is_active(state) is active


def test_state_sets_partition_correctly():
    assert EXECUTING_STATES.isdisjoint(TERMINAL_STATES)
    assert ACTIVE_STATES == EXECUTING_STATES | {RunStatus.PENDING}
    assert ACTIVE_STATES | TERMINAL_STATES == set(RunStatus)


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        (RunTrigger.COMPLETE, RunStatus.SUCCEEDED),
        (RunTrigger.FAIL, RunStatus.FAILED),
        (RunTrigger.CANCEL, RunStatus.CANCELLED),
        (RunTrigger.TIMEOUT, RunStatus.FAILED),
    ],
)
def test_an_outcome_ends_a_run_from_every_live_state(trigger, expected):
    for current in RunStatus:
        if is_terminal(current):
            continue
        assert next_state(current, trigger) is expected


def test_try_next_state_returns_none_for_a_report_that_does_not_apply():
    assert try_next_state(RunStatus.RUNNING, RunTrigger.ACK) is None
    assert try_next_state(RunStatus.SUCCEEDED, RunTrigger.COMPLETE) is None
    assert try_next_state(RunStatus.PAUSED, RunTrigger.COMPLETE) is RunStatus.SUCCEEDED
