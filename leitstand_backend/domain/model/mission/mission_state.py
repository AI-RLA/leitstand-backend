"""Robot -> backend state channel: mission state, stage states, error envelopes."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.stage_status import StageStatus


class MissionExecStatus(str, Enum):
    """The robot's execution status: live (RUNNING/PAUSED) or terminal.

    Execution truth, not the mission lifecycle status (which the backend owns and
    derives from this). SUCCEEDED, FAILED, and CANCELLED are terminal.
    """

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ErrorSeverity(str, Enum):
    """Severity of an error reported by the robot."""

    WARNING = "WARNING"
    FATAL = "FATAL"


class ErrorOrigin(str, Enum):
    """Source of a mission error: the robot's execution, or a backend-authored cause."""

    ROBOT = "robot"
    BACKEND = "backend"


class ErrorReference(BaseModel):
    """Key/value pair attaching context to a :class:`MissionError`."""

    key: str
    value: str


class MissionError(BaseModel):
    """A structured error report attached to a stage or mission.

    Mirrors the proto ``Error`` (severity / type / references / description); ``origin``
    is a backend-only marker distinguishing a robot fault from a backend-authored cause.
    """

    origin: ErrorOrigin
    severity: ErrorSeverity
    type: str = Field(
        description="Stable identifier for programmatic dispatch (e.g. ``boundary_anchor_mismatch``).",
    )
    references: list[ErrorReference] = Field(default_factory=list)
    description: str


class StageState(BaseModel):
    """The robot's view of a single stage's execution state."""

    stage_id: UUID
    status: StageStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    progress: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of stage work completed.",
    )
    result: dict[str, str] | None = None


class MissionStateMessage(BaseModel):
    """Periodic + event-driven snapshot of a run, published by the robot.

    The robot names the run it is executing; it never knows or needs the mission's own id.
    """

    run_id: UUID
    header_id: int = Field(
        ge=0,
        description=(
            "Monotone counter per Zenoh key, scoped to a robot's mission-state "
            "channel. Lets consumers detect dropped messages."
        ),
    )
    timestamp: datetime
    exec_status: MissionExecStatus = Field(
        description="The robot's execution status: live (RUNNING/PAUSED) or terminal "
        "(SUCCEEDED/FAILED/CANCELLED). Execution truth; the backend derives the mission's "
        "lifecycle status from this."
    )
    current_stage_index: int = Field(ge=0)
    stage_states: list[StageState] = Field(default_factory=list)
    errors: list[MissionError] = Field(default_factory=list)
