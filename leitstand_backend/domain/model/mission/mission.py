"""Mission, Stage hierarchy, and the mission-lifecycle / stage-kind enums."""

from datetime import datetime
from enum import Enum
from typing import Literal
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


Stage = NavigationStage


# Resolves the forward reference to ``Stage`` in MissionStageBase.on_cancel,
# which cannot be evaluated until ``Stage`` is bound above. Pydantic v2 requires
# an explicit rebuild call once the referenced symbol exists.
MissionStageBase.model_rebuild()
NavigationStage.model_rebuild()


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
        for waypoint in stage.waypoints:
            if isinstance(waypoint, SiteLocalWaypoint):
                ids.add(waypoint.site_id)
        if stage.on_cancel:
            ids.update(referenced_site_ids(stage.on_cancel))
    return ids
