"""Mission, Stage hierarchy, and the mission-lifecycle / stage-kind enums."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, Waypoint


class MissionStatus(str, Enum):
    """Lifecycle state of a Mission as tracked by the backend."""

    DRAFT = "DRAFT"
    ASSIGNED = "ASSIGNED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageKind(str, Enum):
    """Operation kind a Stage represents."""

    NAVIGATION = "navigation"
    COVERAGE = "coverage"


class MissionStageBase(BaseModel):
    """Common envelope fields shared by every Stage variant."""

    stage_id: UUID
    on_cancel: list["Stage"] | None = Field(
        default=None,
        description=(
            "Cleanup stages executed sequentially when this stage is cancelled. "
            "Cleanup stages are themselves non-cancellable."
        ),
    )


class NavigationStage(MissionStageBase):
    """Drive the robot through an ordered list of waypoints."""

    kind: Literal["navigation"] = "navigation"
    waypoints: list[Waypoint] = Field(
        min_length=1,
        description=(
            "Ordered waypoints to traverse. All waypoints in one stage must share "
            "their ``kind`` (homogeneity); this is enforced by the backend at "
            "dispatch, not by this schema."
        ),
    )


class Segment(BaseModel):
    """One swath across a field, or the turn joining two of them.

    The distinction is the reason coverage is a stage kind of its own. The ground under a swath is
    what gets worked, so a robot that took any convenient path between the same two endpoints would
    leave the strip beside it unworked; the ground under a turn is worked by nothing.
    """

    kind: Literal["swath", "turn"]
    waypoints: list[Waypoint] = Field(
        min_length=2,
        description=(
            "The line to drive, in order. Two waypoints describe a straight run; more describe a "
            "curve, which cannot be recovered from its endpoints."
        ),
    )


class CoverageStage(MissionStageBase):
    """Cover a field by driving its swaths in order, each reached by the turn before it."""

    kind: Literal["coverage"] = "coverage"
    segments: list[Segment] = Field(
        min_length=1,
        description=(
            "The whole stage as one ordered route. Concatenating the segments gives the drivable "
            "line, turns included; the kinds say which of it is a worked swath. Consecutive "
            "segments share an endpoint, which a receiver joining them skips."
        ),
    )


Stage = Annotated[NavigationStage | CoverageStage, Field(discriminator="kind")]


def stage_waypoints(stage: Any) -> list[Waypoint]:
    """Every waypoint a stage will drive, in order, whatever shape it carries them in.

    Lets checks that care only about the points (which frames they use, which sites they name)
    stay indifferent to how a stage groups them. Reads the shape rather than the type, so the
    request-side stage models satisfy it too without the domain having to know they exist.
    """
    segments = getattr(stage, "segments", None)
    if segments is None:
        return stage.waypoints
    # Turns are included: they are driven too, so leaving them out would let waypoints reach the
    # robot that no check has seen.
    return [waypoint for segment in segments for waypoint in segment.waypoints]


# Resolves the forward reference to ``Stage`` in MissionStageBase.on_cancel,
# which cannot be evaluated until ``Stage`` is bound above. Pydantic v2 requires
# an explicit rebuild call once the referenced symbol exists.
MissionStageBase.model_rebuild()
NavigationStage.model_rebuild()
CoverageStage.model_rebuild()


class Mission(BaseModel):
    """A mission: an ordered sequence of stages dispatched to a single robot."""

    mission_id: UUID
    update_id: int = Field(
        default=0,
        ge=0,
        description=(
            "Monotone counter for mid-execution mission updates. Always 0 until "
            "stitching support lands."
        ),
    )
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stages: list[Stage] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


def referenced_site_ids(stages: list[Stage]) -> set[UUID]:
    """Site ids referenced by ``site_local`` waypoints anywhere in ``stages``.

    Recurses into ``on_cancel`` cleanup stages (which may themselves nest), so a
    site referenced only inside a cleanup branch is still captured.
    """
    ids: set[UUID] = set()
    for stage in stages:
        for waypoint in stage_waypoints(stage):
            if isinstance(waypoint, SiteLocalWaypoint):
                ids.add(waypoint.site_id)
        if stage.on_cancel:
            ids.update(referenced_site_ids(stage.on_cancel))
    return ids
