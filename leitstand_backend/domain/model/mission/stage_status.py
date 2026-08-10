"""Per-stage status: the backend's canonical view of a single stage's outcome."""

from enum import Enum


class StageStatus(str, Enum):
    """Canonical per-stage status, covering both robot-reported and backend-resolved values.

    The robot reports ``WAITING`` through ``FAILED`` on the wire. ``CANCELLED`` and
    ``SKIPPED`` are backend-only: the robot cannot express them (it reports a cancelled
    goal as ``FAILED`` and never reports a stage it did not reach), so the backend assigns
    them when it resolves the final per-stage view at the terminal mission transition.
    Inbound frames carry only the wire values; the anti-corruption mapper rejects any other.
    """

    WAITING = "WAITING"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
