"""Run lifecycle: the transition table, its helpers, and the trigger a robot report means."""

from enum import Enum
from typing import Final, Literal

from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.mission_state import MissionExecStatus
from leitstand_backend.domain.model.mission.run_status import RunStatus


class RunTrigger(str, Enum):
    """A lifecycle transition trigger: the named cause of a status change.

    The ``*_REQUEST`` triggers are the operator asking; ``PAUSE``, ``RESUME``, ``ACK``,
    ``COMPLETE``, ``FAIL`` and ``CANCEL`` are the robot reporting; the rest are the backend
    settling a reply, a silence or a robot that came back without the run.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    TIMEOUT = "timeout"
    DISPATCH_UNCONFIRMED = "dispatch_unconfirmed"
    RECONCILE = "reconcile"
    PAUSE_REQUEST = "pause_request"
    RESUME_REQUEST = "resume_request"
    CANCEL_REQUEST = "cancel_request"
    ACK = "ack"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


Actor = Literal["operator", "robot", "backend"]

_OPERATOR_TRIGGERS: Final[frozenset[RunTrigger]] = frozenset(
    {RunTrigger.PAUSE_REQUEST, RunTrigger.RESUME_REQUEST, RunTrigger.CANCEL_REQUEST}
)
_BACKEND_TRIGGERS: Final[frozenset[RunTrigger]] = frozenset(
    {
        RunTrigger.ACCEPT,
        RunTrigger.REJECT,
        RunTrigger.TIMEOUT,
        RunTrigger.DISPATCH_UNCONFIRMED,
        RunTrigger.RECONCILE,
    }
)


def actor_of(trigger: RunTrigger) -> Actor:
    """Who caused a transition: the operator, the robot's report, or the backend."""
    if trigger in _OPERATOR_TRIGGERS:
        return "operator"
    if trigger in _BACKEND_TRIGGERS:
        return "backend"
    return "robot"


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
    (RunStatus.CANCELLING, RunTrigger.REJECT): RunStatus.CANCELLED,
}


# An operator's request holds the run in a transitional state until the robot reports it.
_REQUEST_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    (RunStatus.DISPATCHED, RunTrigger.PAUSE_REQUEST): RunStatus.PAUSING,
    (RunStatus.RUNNING, RunTrigger.PAUSE_REQUEST): RunStatus.PAUSING,
    (RunStatus.RESUMING, RunTrigger.PAUSE_REQUEST): RunStatus.PAUSING,
    (RunStatus.PAUSED, RunTrigger.RESUME_REQUEST): RunStatus.RESUMING,
    (RunStatus.PAUSING, RunTrigger.RESUME_REQUEST): RunStatus.RESUMING,
    **{
        (state, RunTrigger.CANCEL_REQUEST): RunStatus.CANCELLING
        for state in (
            RunStatus.PENDING,
            RunStatus.DISPATCHED,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.PAUSING,
            RunStatus.RESUMING,
            RunStatus.CANCELLING,
        )
    },
}


# Progress the robot reports. It may report before the dispatch reply is recorded, because its
# executor starts before it answers the dispatch query. (PAUSING, ACK) and (RESUMING, PAUSE) are
# absent: the robot has not applied the request yet.
_PROGRESS_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    (RunStatus.PENDING, RunTrigger.ACK): RunStatus.RUNNING,
    (RunStatus.DISPATCHED, RunTrigger.ACK): RunStatus.RUNNING,
    (RunStatus.RESUMING, RunTrigger.ACK): RunStatus.RUNNING,
    # A lost RUNNING frame would otherwise leave a paused machine in a state neither pause nor
    # resume can leave; PENDING is excluded because no robot has accepted such a run yet.
    (RunStatus.DISPATCHED, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.RUNNING, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.PAUSING, RunTrigger.PAUSE): RunStatus.PAUSED,
    (RunStatus.PAUSED, RunTrigger.RESUME): RunStatus.RUNNING,
}


# A robot that came back without a run: the run is closed on the backend's authority.
_RECONCILE_TRANSITIONS: Final[dict[tuple[RunStatus, RunTrigger], RunStatus]] = {
    (RunStatus.CANCELLING, RunTrigger.RECONCILE): RunStatus.CANCELLED,
    **{
        (state, RunTrigger.RECONCILE): RunStatus.FAILED
        for state in (
            RunStatus.PENDING,
            RunStatus.DISPATCHED,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.PAUSING,
            RunStatus.RESUMING,
        )
    },
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
    **_REQUEST_TRANSITIONS,
    **_PROGRESS_TRANSITIONS,
    **_RECONCILE_TRANSITIONS,
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
        RunStatus.PAUSING,
        RunStatus.RESUMING,
        RunStatus.CANCELLING,
    }
)


ACTIVE_STATES: Final[frozenset[RunStatus]] = EXECUTING_STATES | {RunStatus.PENDING}

CONFIRMING_STATES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.PAUSING, RunStatus.RESUMING, RunStatus.CANCELLING}
)


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


def is_confirming(state: RunStatus) -> bool:
    """Return True while an operator's request awaits the robot's report."""
    return state in CONFIRMING_STATES


def is_outcome(exec_status: MissionExecStatus) -> bool:
    """True when a report says how the run ended rather than that it is still going."""
    return exec_status in (
        MissionExecStatus.SUCCEEDED,
        MissionExecStatus.FAILED,
        MissionExecStatus.CANCELLED,
    )


def trigger_for_report(current: RunStatus, exec_status: MissionExecStatus) -> RunTrigger | None:
    """Map the robot's execution status onto a lifecycle trigger, or None.

    The robot is authoritative for how a run is going and how it ended, so an outcome applies
    from any live state and the transition table alone decides whether it does.
    """
    if exec_status is MissionExecStatus.SUCCEEDED:
        return RunTrigger.COMPLETE
    if exec_status is MissionExecStatus.FAILED:
        return RunTrigger.FAIL
    if exec_status is MissionExecStatus.CANCELLED:
        return RunTrigger.CANCEL
    if exec_status is MissionExecStatus.RUNNING:
        return RunTrigger.RESUME if current is RunStatus.PAUSED else RunTrigger.ACK
    if exec_status is MissionExecStatus.PAUSED:
        return RunTrigger.PAUSE
    return None
