"""Mission definition: the Stage hierarchy and the Mission that orders it."""

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from leitstand_backend.domain.model.mission.coverage import CoverageProvenance
from leitstand_backend.domain.model.mission.waypoint import SiteLocalWaypoint, Waypoint


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
    """Cover a field by driving its swaths in order, each reached by the turn before it.

    Provenance is required, so this stage cannot be written by hand: swaths come from the
    planner, and the checks that refuse a robot which would cut the corners read the parameters
    the plan was made with.
    """

    kind: Literal["coverage"] = "coverage"
    segments: list[Segment] = Field(
        min_length=1,
        description=(
            "The whole stage as one ordered route. Concatenating the segments gives the drivable "
            "line, turns included; the kinds say which of it is a worked swath. Consecutive "
            "segments share an endpoint, which a receiver joining them skips."
        ),
    )
    provenance: CoverageProvenance = Field(
        description="The inputs, measurements and act that produced these segments."
    )


Stage = Annotated[NavigationStage | CoverageStage, Field(discriminator="kind")]


def stage_ids(stages: Sequence["Stage"]) -> set[UUID]:
    """Every stage's id, cleanup stages included."""
    found: set[UUID] = set()
    for stage in stages:
        found.add(stage.stage_id)
        if stage.on_cancel:
            found |= stage_ids(stage.on_cancel)
    return found


def replace_stage(stages: list["Stage"], replacement: "Stage") -> tuple[list["Stage"], bool]:
    """Return ``stages`` with the stage carrying ``replacement``'s id swapped for it.

    Cleanup stages are searched too.
    """
    found = False
    out: list["Stage"] = []
    for stage in stages:
        if stage.stage_id == replacement.stage_id:
            out.append(replacement)
            found = True
            continue
        if stage.on_cancel:
            nested, nested_found = replace_stage(stage.on_cancel, replacement)
            if nested_found:
                stage = stage.model_copy(update={"on_cancel": nested})
                found = True
        out.append(stage)
    return out, found


def referenced_site_ids(stages: list[Stage]) -> set[UUID]:
    """Return every site a stage drives in, cleanup stages included."""
    ids: set[UUID] = set()
    for stage in stages:
        for waypoint in stage_waypoints(stage):
            if isinstance(waypoint, SiteLocalWaypoint):
                ids.add(waypoint.site_id)
        if stage.on_cancel:
            ids.update(referenced_site_ids(stage.on_cancel))
    return ids


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
    """A mission definition: an ordered sequence of stages that can be run any number of times.

    Execution lives on :class:`MissionRun`. ``assigned_robot_id`` is the default robot a run goes
    to when the dispatch names none; a run records the robot it actually went to.
    """

    mission_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stages: list[Stage] = Field(min_length=1)
    assigned_robot_id: str | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
