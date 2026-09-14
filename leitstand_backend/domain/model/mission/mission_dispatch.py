"""How a robot stops when a run is cancelled."""

from enum import Enum


class CancelMode(str, Enum):
    """How the robot should react to a cancel request."""

    GRACEFUL = "graceful"
    IMMEDIATE = "immediate"
