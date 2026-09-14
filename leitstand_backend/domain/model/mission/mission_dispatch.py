"""What the backend tells a robot about a run, and how the robot may refuse."""

from enum import Enum


class CancelMode(str, Enum):
    """How the robot should react to a cancel request."""

    GRACEFUL = "graceful"
    IMMEDIATE = "immediate"


class ControlRefusal(str, Enum):
    """Why a robot did not apply a pause, resume or cancel."""

    NOT_EXECUTING_RUN = "not_executing_run"
    OTHER = "other"
    UNSPECIFIED = "unspecified"
