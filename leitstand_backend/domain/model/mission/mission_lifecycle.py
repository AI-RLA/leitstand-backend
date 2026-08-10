"""Mission lifecycle state machine: explicit transition table + transition helper."""

from enum import Enum
from typing import Final

from leitstand_backend.domain.errors import InvalidMissionTransition
from leitstand_backend.domain.model.mission.mission import MissionStatus


class MissionTrigger(str, Enum):
    """A lifecycle transition trigger: the named cause of a status change."""

    ASSIGN = "assign"
    UNASSIGN = "unassign"
    DISPATCH = "dispatch"
    ACK = "ack"
    REJECT = "reject"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"
    RESET = "reset"


ALLOWED_TRANSITIONS: Final[dict[tuple[MissionStatus, MissionTrigger], MissionStatus]] = {
    (MissionStatus.DRAFT, MissionTrigger.ASSIGN): MissionStatus.ASSIGNED,
    (MissionStatus.DRAFT, MissionTrigger.DISPATCH): MissionStatus.DISPATCHED,
    (MissionStatus.ASSIGNED, MissionTrigger.UNASSIGN): MissionStatus.DRAFT,
    (MissionStatus.ASSIGNED, MissionTrigger.DISPATCH): MissionStatus.DISPATCHED,
    (MissionStatus.DISPATCHED, MissionTrigger.ACK): MissionStatus.RUNNING,
    (MissionStatus.DISPATCHED, MissionTrigger.REJECT): MissionStatus.FAILED,
    (MissionStatus.DISPATCHED, MissionTrigger.COMPLETE): MissionStatus.SUCCEEDED,
    (MissionStatus.RUNNING, MissionTrigger.PAUSE): MissionStatus.PAUSED,
    (MissionStatus.PAUSED, MissionTrigger.RESUME): MissionStatus.RUNNING,
    (MissionStatus.RUNNING, MissionTrigger.COMPLETE): MissionStatus.SUCCEEDED,
    (MissionStatus.RUNNING, MissionTrigger.FAIL): MissionStatus.FAILED,
    (MissionStatus.PAUSED, MissionTrigger.FAIL): MissionStatus.FAILED,
    (MissionStatus.DRAFT, MissionTrigger.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.ASSIGNED, MissionTrigger.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.DISPATCHED, MissionTrigger.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.RUNNING, MissionTrigger.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.PAUSED, MissionTrigger.CANCEL): MissionStatus.CANCELLED,
    (MissionStatus.FAILED, MissionTrigger.RESET): MissionStatus.DRAFT,
    (MissionStatus.CANCELLED, MissionTrigger.RESET): MissionStatus.DRAFT,
}


TERMINAL_STATES: Final[frozenset[MissionStatus]] = frozenset(
    {
        MissionStatus.SUCCEEDED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    }
)


EXECUTING_STATES: Final[frozenset[MissionStatus]] = frozenset(
    {
        MissionStatus.DISPATCHED,
        MissionStatus.RUNNING,
        MissionStatus.PAUSED,
    }
)


def next_state(current: MissionStatus, trigger: MissionTrigger) -> MissionStatus:
    """Return the state reached by applying ``trigger`` to ``current``.

    Raises :class:`InvalidMissionTransition` when the pair is not in
    :data:`ALLOWED_TRANSITIONS`.
    """
    try:
        return ALLOWED_TRANSITIONS[(current, trigger)]
    except KeyError as exc:
        raise InvalidMissionTransition(current, trigger) from exc


def is_terminal(state: MissionStatus) -> bool:
    """Return True if ``state`` has no outbound transitions."""
    return state in TERMINAL_STATES


def is_executing(state: MissionStatus) -> bool:
    """Return True while a robot is actively running the mission.

    Executing means DISPATCHED, RUNNING, or PAUSED. Distinct from "non-terminal":
    ASSIGNED is non-terminal but not yet executing (the robot has not been sent the goal).
    """
    return state in EXECUTING_STATES
