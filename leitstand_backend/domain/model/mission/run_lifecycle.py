"""Run lifecycle state machine: explicit transition table + transition helper."""

from enum import Enum
from typing import Final

from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.run_status import RunStatus


class RunTrigger(str, Enum):
    """A lifecycle transition trigger: the named cause of a status change.

    ``ACCEPT``, ``REJECT`` and ``TIMEOUT`` come from the backend's handling of the dispatch reply;
    the rest come from the robot's reports, except ``CANCEL``, which either side may cause.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    TIMEOUT = "timeout"
    ACK = "ack"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


TERMINAL_STATES: Final[frozenset[RunStatus]] = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.REJECTED,
    }
)


_LIVE_STATES: Final[tuple[RunStatus, ...]] = tuple(
    state for state in RunStatus if state not in TERMINAL_STATES
)


# Settling the dispatch reply, where the backend is the authority.
_REPLY_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    (RunStatus.PENDING, RunTrigger.ACCEPT): RunStatus.DISPATCHED,
    (RunStatus.PENDING, RunTrigger.REJECT): RunStatus.REJECTED,
}


# Progress the robot reports. It may report before the dispatch reply is recorded, because its
# executor starts before it answers the dispatch query.
_PROGRESS_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    (RunStatus.PENDING, RunTrigger.ACK): RunStatus.RUNNING,
    (RunStatus.DISPATCHED, RunTrigger.ACK): RunStatus.RUNNING,
    # A lost RUNNING frame would otherwise leave a paused machine in a state neither pause nor
    # resume can leave; PENDING is excluded because no robot has accepted such a run yet.
    (RunStatus.DISPATCHED, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.RUNNING, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.RESUME): RunStatus.RUNNING,
}


# An outcome ends a run from any live state. State frames may be dropped under load, so a
# run whose RUNNING frame was lost must still be ended by the frame that reports the outcome.
_OUTCOMES: Final[dict[RunTrigger, RunStatus]] = {
    RunTrigger.COMPLETE: RunStatus.SUCCEEDED,
    RunTrigger.FAIL: RunStatus.FAILED,
    RunTrigger.CANCEL: RunStatus.CANCELLED,
    RunTrigger.TIMEOUT: RunStatus.FAILED,
}


ALLOWED_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    **_REPLY_TRANSITIONS,
    **_PROGRESS_TRANSITIONS,
    **{
        (state, trigger): outcome
        for trigger, outcome in _OUTCOMES.items()
        for state in _LIVE_STATES
    },
}


# The robot has, or may have, the goal; PENDING is excluded because the dispatch is unanswered.
EXECUTING_STATES: Final[frozenset[RunStatus]] = frozenset(
    {
        RunStatus.DISPATCHED,
        RunStatus.RUNNING,
        RunStatus.PAUSED,
    }
)


ACTIVE_STATES: Final[frozenset[RunStatus]] = EXECUTING_STATES | {RunStatus.PENDING}


def next_state(current: RunStatus, trigger: RunTrigger) -> RunStatus:
    """Return the state reached by applying ``trigger`` to ``current``.

    Raises :class:`InvalidMissionTransition` when the pair is not in
    :data:`ALLOWED_TRANSITIONS`.
    """
    try:
        return ALLOWED_TRANSITIONS[(current, trigger)]
    except KeyError as exc:
        raise InvalidMissionTransition(current, trigger) from exc


def try_next_state(current: RunStatus, trigger: RunTrigger) -> RunStatus | None:
    """Return the state reached by applying ``trigger``, or None when it does not apply.

    For a report the robot repeats, such as RUNNING while already running, not applying is the
    expected outcome rather than an error.
    """
    return ALLOWED_TRANSITIONS.get((current, trigger))


def is_terminal(state: RunStatus) -> bool:
    """Return True if ``state`` has no outbound transitions."""
    return state in TERMINAL_STATES


def is_executing(state: RunStatus) -> bool:
    """Return True while the robot has, or may have, the goal."""
    return state in EXECUTING_STATES


def is_active(state: RunStatus) -> bool:
    """Return True while the run occupies its robot and counts against the mission."""
    return state in ACTIVE_STATES
