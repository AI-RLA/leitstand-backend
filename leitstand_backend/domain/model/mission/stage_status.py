"""Per-stage status: the backend's canonical view of a single stage's outcome."""

from enum import Enum


class StageStatus(str, Enum):
    """Canonical per-stage status, covering both robot-reported and backend-resolved values.

    A robot reports the stage a cancel interrupted as ``CANCELLED`` and the stages it never
    started as ``SKIPPED``; the backend assigns the same two when it closes a run the robot did
    not report to the end.
    """

    WAITING = "WAITING"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
