"""Run lifecycle status: the backend's own view of one execution of a mission."""

from enum import Enum


class RunStatus(str, Enum):
    """Lifecycle state of a MissionRun, owned by the backend.

    ``PENDING`` is the run row committed before the robot has answered the dispatch. ``REJECTED``
    is kept apart from ``FAILED`` because they mean different things for the robot: a rejection
    means it is definitely not driving, while a timeout or a mid-run failure means it may be.
    ``PAUSING``, ``RESUMING`` and ``CANCELLING`` hold an operator's request until the robot's own
    report confirms it.
    """

    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    PAUSING = "PAUSING"
    RESUMING = "RESUMING"
    CANCELLING = "CANCELLING"
